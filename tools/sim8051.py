#!/usr/bin/env python3
"""Minimal 8051 interpreter for the rtl8192cu MAC firmware image.

The chip has no debugger port; this simulator is the only way to watch the
fw run. V1 scope: full 8051 core (all 256 opcodes, PSW flags, register
banks), the image mapped at 0x4000, boot-ROM calls (0x0000-0x3FFF) trapped
and stubbed, MAC registers (XDATA 0x0000-0x0FFF) instrumented, known halt
slots detected, and an H2C mailbox injector to drive the command path.

Known ROM helpers are emulated from their reverse-engineered semantics
(see analysis/findings.md); everything else in ROM logs and returns.

Usage:
  python3 -I sim8051.py IMG [--base 0x4000] [--steps N] [--trace]
                          [--h2c ID:B1:B2:B3] [--h2c-at N] [--rom-stop]
"""
import argparse
import importlib.util
import sys

# reuse the disassembler's decode table for tracing
_spec = importlib.util.spec_from_file_location('dis8051', __file__.rsplit('/', 1)[0] + '/dis8051.py')
dis = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dis)

BASE = 0x4000
HALT_SLOTS = {0x4503, 0x4506, 0x62F1}

# generic-pointer spaces (Keil C51 ABI, verified against 0x4220)
PTR_SPACE = {0x00: 'idata', 0x01: 'xdata', 0xFE: 'pdata', 0xFF: 'code'}


class Sim:
    def __init__(self, img, base=BASE, rom_stop=False):
        self.img = img
        self.base = base
        self.rom_stop = rom_stop
        self.idata = bytearray(256)
        self.sfr = bytearray(128)          # direct 0x80-0xFF
        self.xdata = bytearray(0x10000)
        self.sfr[0x81 - 0x80] = 0x07       # SP
        self.pc = 0
        self.rom_calls = {}                # addr -> count
        self.reg_writes = []               # (step, addr, val) XDATA < 0x1000
        self.steps = 0
        self.halted = None

    # --- memory -------------------------------------------------------
    def rd_code(self, a):
        if self.base <= a < self.base + len(self.img):
            return self.img[a - self.base]
        return None

    def rd_dir(self, a):
        return self.sfr[a - 0x80] if a >= 0x80 else self.idata[a]

    def wr_dir(self, a, v):
        if a >= 0x80:
            self.sfr[a - 0x80] = v
        else:
            self.idata[a] = v

    def rd_bit(self, b):
        if b >= 0x80:                        # SFR bit: base = b & 0xF8
            return (self.sfr[(b & 0xF8) - 0x80] >> (b & 7)) & 1
        return (self.idata[0x20 + (b >> 3)] >> (b & 7)) & 1

    def wr_bit(self, b, v):
        if b >= 0x80:
            a = (b & 0xF8) - 0x80
            self.sfr[a] = (self.sfr[a] & ~(1 << (b & 7))) | (v << (b & 7))
        else:
            a = 0x20 + (b >> 3)
            self.idata[a] = (self.idata[a] & ~(1 << (b & 7))) | (v << (b & 7))

    @property
    def psw(self):
        return self.sfr[0xD0 - 0x80]

    @psw.setter
    def psw(self, v):
        self.sfr[0xD0 - 0x80] = v

    @property
    def dptr(self):
        return (self.sfr[0x83 - 0x80] << 8) | self.sfr[0x82 - 0x80]

    @dptr.setter
    def dptr(self, v):
        self.sfr[0x83 - 0x80] = (v >> 8) & 0xFF
        self.sfr[0x82 - 0x80] = v & 0xFF

    def acc(self):
        return self.sfr[0xE0 - 0x80]

    def set_acc(self, v):
        self.sfr[0xE0 - 0x80] = v & 0xFF

    setA = set_acc

    def b(self):
        return self.sfr[0xF0 - 0x80]

    def set_b(self, v):
        self.sfr[0xF0 - 0x80] = v & 0xFF

    def sp(self):
        return self.sfr[0x81 - 0x80]

    def set_sp(self, v):
        self.sfr[0x81 - 0x80] = v

    def bank(self, i):                      # R0-R7 via PSW bank select
        return self.idata[((self.psw >> 3) & 3) * 8 + i]

    def set_bank(self, i, v):
        self.idata[((self.psw >> 3) & 3) * 8 + i] = v & 0xFF

    def cy(self):
        return self.psw & 1

    def set_flag(self, bit, v):
        self.psw = (self.psw & ~bit) | (bit if v else 0)

    # --- stack --------------------------------------------------------
    def push(self, v):
        sp = self.sp() + 1
        self.set_sp(sp & 0xFF)
        self.idata[sp & 0xFF] = v & 0xFF

    def pop(self):
        sp = self.sp()
        v = self.idata[sp]
        self.set_sp((sp - 1) & 0xFF)
        if sp == 0:
            self.halted = 'stack underflow'
        return v

    # --- generic pointer (Keil ABI) -----------------------------------
    def gptr_read(self, r3, r2, r1, off):
        sp_ = PTR_SPACE.get(r3, 'xdata')
        a = ((r2 << 8) | r1) + off
        if sp_ == 'xdata':
            return self.xdata[a & 0xFFFF]
        if sp_ == 'idata':
            return self.idata[a & 0xFF]
        if sp_ == 'pdata':
            return self.xdata[a & 0xFF]
        return self.rd_code(a) or 0          # code

    # --- ROM stubs ----------------------------------------------------
    def rom_call(self, addr):
        self.rom_calls[addr] = self.rom_calls.get(addr, 0) + 1
        if self.rom_stop:
            self.halted = f'ROM call {addr:04X} (rom-stop)'
            return False
        r3, r2, r1 = self.bank(3), self.bank(2), self.bank(1)
        if addr == 0x29D9:                  # generic-ptr byte read
            self.set_acc(self.gptr_read(r3, r2, r1, 0))
        elif addr == 0x3754:                # delay service: return
            pass
        elif addr in (0x2A7F, 0x2FD9):      # read/convert helpers: return A=0
            self.set_acc(0)
        else:
            pass                            # log-only: return unchanged
        return True                         # RET behavior: pop PC

    # --- the core -----------------------------------------------------
    def step(self):
        pc = self.pc
        op = self.rd_code(pc)
        if op is None:
            if pc < self.base:              # boot ROM
                ok = self.rom_call(pc)
                if not ok:
                    return False
                self.pc = (self.pop() << 8) | self.pop()
                return True
            self.halted = f'fetch outside image at {pc:04X}'
            return False
        self.steps += 1
        img = self.img
        off = pc - self.base
        nxt = pc + 1
        rd = self.rd_code

        def rel(a, b):
            return a + (b - 256 if b > 127 else b)

        # operand fetchers
        i8 = lambda a: rd(a)

        A = self.acc

        def setA(v):
            self.set_acc(v)

        if op == 0x00:
            pass
        elif op in (0x01, 0x21, 0x41, 0x61, 0x81, 0xA1, 0xC1, 0xE1):  # AJMP
            t = ((pc + 2) & 0xF800) | ((op & 0xE0) << 3) | rd(pc + 1)
            self.pc = t
            return self.chk_halt(t)
        elif op in (0x11, 0x31, 0x51, 0x71, 0x91, 0xB1, 0xD1, 0xF1):  # ACALL
            t = ((pc + 2) & 0xF800) | ((op & 0xE0) << 3) | rd(pc + 1)
            ra = pc + 2
            self.push(ra & 0xFF)
            self.push(ra >> 8)
            self.pc = t
            return self.chk_halt(t)
        elif op == 0x02:                    # LJMP
            t = (rd(pc + 1) << 8) | rd(pc + 2)
            self.pc = t
            return self.chk_halt(t)
        elif op == 0x12:                    # LCALL
            t = (rd(pc + 1) << 8) | rd(pc + 2)
            ra = pc + 3
            self.push(ra & 0xFF)
            self.push(ra >> 8)
            self.pc = t
            return self.chk_halt(t)
        elif op == 0x22:                    # RET
            self.pc = (self.pop() << 8) | self.pop()
            return True
        elif op == 0x32:                    # RETI
            self.pc = (self.pop() << 8) | self.pop()
            return True
        elif op == 0x03:                    # RR A
            a = A()
            self.setA((a >> 1) | ((a & 1) << 7))
        elif op == 0x13:                    # RRC A
            a = A()
            c = self.cy()
            self.set_flag(1, a & 1)
            self.setA((a >> 1) | (c << 7))
        elif op == 0x23:                    # RL A
            a = A()
            self.setA(((a << 1) & 0xFF) | (a >> 7))
        elif op == 0x33:                    # RLC A
            a = A()
            c = self.cy()
            self.set_flag(1, a >> 7)
            self.setA(((a << 1) & 0xFF) | c)
        elif op == 0x04:
            self.setA(A() + 1)
        elif op == 0x14:
            self.setA(A() - 1)
        elif op == 0x05:
            self.wr_dir(rd(pc + 1), (self.rd_dir(rd(pc + 1)) + 1) & 0xFF)
            nxt += 1
        elif op == 0x15:
            self.wr_dir(rd(pc + 1), (self.rd_dir(rd(pc + 1)) - 1) & 0xFF)
            nxt += 1
        elif op in (0x06, 0x07):            # INC @Rn
            a = self.bank(op & 1)
            self.idata[a] = (self.idata[a] + 1) & 0xFF
        elif op in (0x16, 0x17):            # DEC @Rn
            a = self.bank(op & 1)
            self.idata[a] = (self.idata[a] - 1) & 0xFF
        elif 0x08 <= op <= 0x0F:
            self.set_bank(op & 7, (self.bank(op & 7) + 1) & 0xFF)
        elif 0x18 <= op <= 0x1F:
            self.set_bank(op & 7, (self.bank(op & 7) - 1) & 0xFF)
        elif op == 0x10:                    # JBC bit,rel
            b = rd(pc + 1)
            if self.rd_bit(b):
                self.wr_bit(b, 0)
                nxt = rel(pc + 3, rd(pc + 2))
            else:
                nxt = pc + 3
        elif op == 0x20:                    # JB
            if self.rd_bit(rd(pc + 1)):
                nxt = rel(pc + 3, rd(pc + 2))
            else:
                nxt = pc + 3
        elif op == 0x30:                    # JNB
            if not self.rd_bit(rd(pc + 1)):
                nxt = rel(pc + 3, rd(pc + 2))
            else:
                nxt = pc + 3
        elif op == 0x40:                    # JC
            if self.cy():
                nxt = rel(pc + 2, rd(pc + 1))
            else:
                nxt = pc + 2
        elif op == 0x50:                    # JNC
            if not self.cy():
                nxt = rel(pc + 2, rd(pc + 1))
            else:
                nxt = pc + 2
        elif op == 0x60:                    # JZ
            if A() == 0:
                nxt = rel(pc + 2, rd(pc + 1))
            else:
                nxt = pc + 2
        elif op == 0x70:                    # JNZ
            if A() != 0:
                nxt = rel(pc + 2, rd(pc + 1))
            else:
                nxt = pc + 2
        elif op == 0x80:                    # SJMP
            nxt = rel(pc + 2, rd(pc + 1))
        elif op == 0x72:                    # ORL C,bit
            self.set_flag(1, self.cy() | self.rd_bit(rd(pc + 1)))
            nxt += 1
        elif op == 0x82:                    # ANL C,bit
            self.set_flag(1, self.cy() & self.rd_bit(rd(pc + 1)))
            nxt += 1
        elif op == 0xA0:                    # ORL C,/bit
            self.set_flag(1, self.cy() | (1 - self.rd_bit(rd(pc + 1))))
            nxt += 1
        elif op == 0xB0:                    # ANL C,/bit
            self.set_flag(1, self.cy() & (1 - self.rd_bit(rd(pc + 1))))
            nxt += 1
        elif op == 0x73:                    # JMP @A+DPTR
            self.pc = (self.dptr + A()) & 0xFFFF
            return True
        elif op == 0x92:                    # MOV bit,C
            self.wr_bit(rd(pc + 1), self.cy())
            nxt += 1
        elif op == 0xA2:                    # MOV C,bit
            self.set_flag(1, self.rd_bit(rd(pc + 1)))
            nxt += 1
        elif op == 0xA3:
            self.dptr = (self.dptr + 1) & 0xFFFF
        elif op == 0xB2:
            self.wr_bit(rd(pc + 1), 1 - self.rd_bit(rd(pc + 1)))
            nxt += 1
        elif op == 0xC2:
            self.wr_bit(rd(pc + 1), 0)
            nxt += 1
        elif op == 0xD2:
            self.wr_bit(rd(pc + 1), 1)
            nxt += 1
        elif op == 0xC3:
            self.set_flag(1, 0)
        elif op == 0xD3:
            self.set_flag(1, 1)
        elif op == 0xB3:
            self.set_flag(1, 1 - self.cy())
        elif op == 0xC0:                    # PUSH
            self.push(self.rd_dir(rd(pc + 1)))
            nxt += 1
        elif op == 0xD0:                    # POP
            self.wr_dir(rd(pc + 1), self.pop())
            nxt += 1
        elif op == 0x83:                    # MOVC A,@A+PC
            self.setA(self.rd_code((A() + pc + 1) & 0xFFFF) or 0)
        elif op == 0x93:                    # MOVC A,@A+DPTR
            self.setA(self.rd_code((A() + self.dptr) & 0xFFFF) or 0)
        elif op == 0xE0:
            self.setA(self.xdata[self.dptr])
        elif op == 0xF0:
            a = self.dptr
            self.xdata[a] = A()
            self.note_xwrite(a, A())
        elif op in (0xE2, 0xE3):
            self.setA(self.xdata[self.bank(op & 1)])
        elif op in (0xF2, 0xF3):
            a = self.bank(op & 1)
            self.xdata[a] = A()
            self.note_xwrite(a, A())
        elif op == 0xE4:
            self.setA(0)
        elif op == 0xF4:
            self.setA(0xFF - A())
        elif op == 0xC4:
            a = A()
            self.setA(((a & 0x0F) << 4) | (a >> 4))
        elif op == 0xD4:                    # DA A
            a = A()
            if (a & 0x0F) > 9 or self.psw & 0x04:
                a += 0x06
            if (a & 0xF0) > 0x90 or self.psw & 0x80:
                a += 0x60
                self.set_flag(1, 1)
            self.setA(a)
        elif op == 0x84:                    # DIV AB
            a, bb = A(), self.b()
            if bb == 0:
                self.set_flag(0x04, 1)
            else:
                self.setA(a // bb)
                self.set_b(a % bb)
                self.set_flag(0x04, 0)
            self.set_flag(1, 0)
        elif op == 0xA4:                    # MUL AB
            v = A() * self.b()
            self.setA(v & 0xFF)
            self.set_b(v >> 8)
            self.set_flag(1, v >> 8)
        elif op in (0x74, 0x75, 0x76, 0x77):  # MOV imm
            v = rd(pc + (2 if op == 0x75 else 1))
            if op == 0x74:
                self.setA(v)
            elif op == 0x75:
                self.wr_dir(rd(pc + 1), v)
            else:
                self.idata[self.bank(op & 1)] = v
            nxt = pc + (3 if op == 0x75 else 2)
        elif 0x78 <= op <= 0x7F:
            self.set_bank(op & 7, rd(pc + 1))
            nxt += 1
        elif op == 0x85:                    # MOV dir,dir (src first)
            self.wr_dir(rd(pc + 2), self.rd_dir(rd(pc + 1)))
            nxt += 2
        elif op in (0x86, 0x87):            # MOV dir,@Rn
            self.wr_dir(rd(pc + 1), self.idata[self.bank(op & 1)])
            nxt += 1
        elif 0x88 <= op <= 0x8F:            # MOV dir,Rn
            self.wr_dir(rd(pc + 1), self.bank(op & 7))
            nxt += 1
        elif op == 0x90:
            self.dptr = (rd(pc + 1) << 8) | rd(pc + 2)
            nxt += 2
        elif op in (0xA6, 0xA7):            # MOV @Rn,dir
            self.idata[self.bank(op & 1)] = self.rd_dir(rd(pc + 1))
            nxt += 1
        elif 0xA8 <= op <= 0xAF:            # MOV Rn,dir
            self.set_bank(op & 7, self.rd_dir(rd(pc + 1)))
            nxt += 1
        elif op == 0xC5:
            a = A()
            self.setA(self.rd_dir(rd(pc + 1)))
            self.wr_dir(rd(pc + 1), a)
            nxt += 1
        elif op in (0xC6, 0xC7):
            a = A()
            r = self.bank(op & 1)
            self.setA(self.idata[r])
            self.idata[r] = a
        elif 0xC8 <= op <= 0xCF:
            a = A()
            self.setA(self.bank(op & 7))
            self.set_bank(op & 7, a)
        elif op in (0xD6, 0xD7):            # XCHD
            r = self.bank(op & 1)
            a, m = A(), self.idata[r]
            self.setA((a & 0xF0) | (m & 0x0F))
            self.idata[r] = (m & 0xF0) | (a & 0x0F)
        elif op in (0xE5, 0xF5):
            if op == 0xE5:
                self.setA(self.rd_dir(rd(pc + 1)))
            else:
                self.wr_dir(rd(pc + 1), A())
            nxt += 1
        elif op in (0xE6, 0xE7):
            self.setA(self.idata[self.bank(op & 1)])
        elif op in (0xF6, 0xF7):
            self.idata[self.bank(op & 1)] = A()
        elif 0xE8 <= op <= 0xEF:
            self.setA(self.bank(op & 7))
        elif 0xF8 <= op <= 0xFF:
            self.set_bank(op & 7, A())
        elif op in (0xB4, 0xB5, 0xB6, 0xB7) or 0xB8 <= op <= 0xBF:  # CJNE
            if op == 0xB4:                  # A,#imm
                v = rd(pc + 1)
                self.set_flag(1, 1 if A() < v else 0)
                taken = A() != v
                o2 = 2
            elif op == 0xB5:                # A,dir
                v = self.rd_dir(rd(pc + 1))
                self.set_flag(1, 1 if A() < v else 0)
                taken = A() != v
                o2 = 2
            elif op in (0xB6, 0xB7):        # @Rn,#imm
                v = rd(pc + 1)
                a = self.idata[self.bank(op & 1)]
                self.set_flag(1, 1 if a < v else 0)
                taken = a != v
                o2 = 2
            else:                           # Rn,#imm
                v = rd(pc + 1)
                a = self.bank(op & 7)
                self.set_flag(1, 1 if a < v else 0)
                taken = a != v
                o2 = 2
            if taken:
                nxt = rel(pc + 3, rd(pc + 2))
            else:
                nxt = pc + 3
        elif op == 0xD5:                    # DJNZ dir,rel
            a = rd(pc + 1)
            v = (self.rd_dir(a) - 1) & 0xFF
            self.wr_dir(a, v)
            nxt = rel(pc + 3, rd(pc + 2)) if v else pc + 3
        elif 0xD8 <= op <= 0xDF:            # DJNZ Rn,rel
            i = op & 7
            self.set_bank(i, (self.bank(i) - 1) & 0xFF)
            nxt = rel(pc + 2, rd(pc + 1)) if self.bank(i) else pc + 2
        # --- arithmetic / logic group (op low nibble symmetric) ---
        elif op in (0x24, 0x25, 0x26, 0x27) or 0x28 <= op <= 0x2F:  # ADD
            v = self._rhs(op, pc)
            self._add(v, carry=False)
            nxt = pc + self._olen(op)
        elif op in (0x34, 0x35, 0x36, 0x37) or 0x38 <= op <= 0x3F:  # ADDC
            v = self._rhs(op, pc)
            self._add(v, carry=True)
            nxt = pc + self._olen(op)
        elif op in (0x94, 0x95, 0x96, 0x97) or 0x98 <= op <= 0x9F:  # SUBB
            v = self._rhs(op, pc)
            self._sub(v)
            nxt = pc + self._olen(op)
        elif op in (0x42, 0x43, 0x44, 0x45, 0x46, 0x47) or 0x48 <= op <= 0x4F:  # ORL
            if op == 0x42:                    # ORL dir,A
                a = rd(pc + 1)
                self.wr_dir(a, self.rd_dir(a) | A())
                nxt = pc + 2
            elif op == 0x43:                  # ORL dir,#imm
                a = rd(pc + 1)
                self.wr_dir(a, self.rd_dir(a) | rd(pc + 2))
                nxt = pc + 3
            else:
                self.setA(A() | self._rhs(op, pc))
                nxt = pc + self._olen(op)
        elif op in (0x52, 0x53, 0x54, 0x55, 0x56, 0x57) or 0x58 <= op <= 0x5F:  # ANL
            if op == 0x52:
                a = rd(pc + 1)
                self.wr_dir(a, self.rd_dir(a) & A())
                nxt = pc + 2
            elif op == 0x53:
                a = rd(pc + 1)
                self.wr_dir(a, self.rd_dir(a) & rd(pc + 2))
                nxt = pc + 3
            else:
                self.setA(A() & self._rhs(op, pc))
                nxt = pc + self._olen(op)
        elif op in (0x62, 0x63, 0x64, 0x65, 0x66, 0x67) or 0x68 <= op <= 0x6F:  # XRL
            if op == 0x62:
                a = rd(pc + 1)
                self.wr_dir(a, self.rd_dir(a) ^ A())
                nxt = pc + 2
            elif op == 0x63:
                a = rd(pc + 1)
                self.wr_dir(a, self.rd_dir(a) ^ rd(pc + 2))
                nxt = pc + 3
            else:
                self.setA(A() ^ self._rhs(op, pc))
                nxt = pc + self._olen(op)
        elif op == 0xA5:
            self.halted = f'undefined opcode A5 at {pc:04X}'
            return False
        else:
            self.halted = f'unimplemented opcode {op:02X} at {pc:04X}'
            return False
        self.pc = nxt & 0xFFFF
        return self.chk_halt(self.pc)

    # helpers for the ALU group
    def _rhs(self, op, pc):
        rd = self.rd_code
        if op in (0x24, 0x34, 0x44, 0x54, 0x64, 0x94):
            return rd(pc + 1)
        if op in (0x25, 0x35, 0x45, 0x55, 0x65, 0x95):
            return self.rd_dir(rd(pc + 1))
        if op in (0x26, 0x36, 0x46, 0x56, 0x66, 0x96):
            return self.idata[self.bank(0)]
        if op in (0x27, 0x37, 0x47, 0x57, 0x67, 0x97):
            return self.idata[self.bank(1)]
        return self.bank(op & 7)

    def _olen(self, op):
        return 2 if op in (0x24, 0x34, 0x44, 0x54, 0x64, 0x94,
                           0x25, 0x35, 0x45, 0x55, 0x65, 0x95) else 1

    def _add(self, v, carry):
        a = self.acc()
        r = a + v + (1 if carry and self.cy() else 0)
        self.set_flag(0x02, ((a & 0x0F) + (v & 0x0F)) > 0x0F)   # AC
        self.set_flag(1, r > 0xFF)
        ov = (~(a ^ v) & (a ^ r) & 0x80) != 0
        self.set_flag(0x04, ov)
        self.set_acc(r)

    def _sub(self, v):
        a = self.acc()
        c = 1 if self.cy() else 0
        r = a - v - c
        self.set_flag(1, 1 if r < 0 else 0)
        ov = ((a ^ v) & (a ^ (r & 0xFF)) & 0x80) != 0
        self.set_flag(0x04, ov)
        self.set_acc(r)

    def note_xwrite(self, a, v):
        if a < 0x1000 and len(self.reg_writes) < 4096:
            self.reg_writes.append((self.steps, a, v))

    def chk_halt(self, t):
        if t in HALT_SLOTS:
            self.halted = f'halt slot {t:04X}'
            return False
        return True

    def run(self, n, trace=False):
        while self.steps < n and not self.halted:
            if trace:
                ins = dis.decode(self.img, self.pc, self.base) if self.base <= self.pc < self.base + len(self.img) else None
                if ins:
                    print(f'{self.steps:7d} {self.pc:04X}  {ins.text}')
                else:
                    print(f'{self.steps:7d} {self.pc:04X}  <ROM {"ptr" if self.pc < self.base else "?"}>')
            self.step()
        return self.halted


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('img')
    ap.add_argument('--base', type=lambda x: int(x, 0), default=BASE)
    ap.add_argument('--steps', type=int, default=200000)
    ap.add_argument('--entry', type=lambda x: int(x, 0), default=0x5559,
                    help='PC to start (default: h2c_service)')
    ap.add_argument('--sp', type=lambda x: int(x, 0), default=0x40)
    ap.add_argument('--trace', action='store_true')
    ap.add_argument('--h2c', help='prefill box 0 with id:b1:b2:b3 (hex)')
    ap.add_argument('--ext', help='prefill EXT regs 0x0088/0x0089 with b0:b1 (hex)')
    ap.add_argument('--fwisr', help='inject interrupt: hex byte(s) for FWISR '
                    '0x0134.. (e.g. 20 = FWISR0.b5 clock-off); enters via '
                    'vector 0x60F2 with a synthetic interrupted-PC frame')
    ap.add_argument('--rom-stop', action='store_true',
                    help='halt on any ROM entry')
    args = ap.parse_args()

    with open(args.img, 'rb') as f:
        img = f.read()
    sim = Sim(img, args.base, args.rom_stop)
    sim.set_sp(args.sp)
    # synthetic return frame: RET/RETI at top level parks at the sentinel
    sim.push(0xDE)
    sim.push(0x7E)                           # 0x7EDE = just past image end
    sim.pc = args.entry
    if args.fwisr:
        b = [int(x, 16) for x in args.fwisr.split(':')]
        for i, v in enumerate(b[:4]):
            sim.xdata[0x0134 + i] = v
        # hardware-style interrupt entry: fw enable masks IDATA 0x28-0x2B
        # default-open in the sim so injected bits reach the dispatcher
        sim.idata[0x28:0x2C] = b'\xff' * 4
        sim.xdata[0x01CF] = b[0]             # 0x01CF mirrors FWISR0 for handlers
        sim.sfr[0xA8 - 0x80] = 0x80          # IE.7 = EA on
        sim.pc = 0x60F2                      # vector slot 0x4013 target

    if args.h2c:
        b = [int(x, 16) for x in args.h2c.split(':')]
        for i, v in enumerate(b[:4]):
            sim.xdata[0x01D0 + i] = v
        if args.ext:
            e = [int(x, 16) for x in args.ext.split(':')]
            sim.xdata[0x0088] = e[0]
            sim.xdata[0x0089] = e[1]
        sim.xdata[0x01CC] = 0x01             # box 0 pending
        sim.xdata[0x8B51] = 0x00             # state ring at box 0
        print(f'H2C box0 = id {b[0]:02X} args {b[1]:4X}'.replace(' ', ''))
        print(f'  bytes: ' + ' '.join(f'{v:02X}' for v in b[:4]))

    h = sim.run(args.steps, trace=args.trace)
    print(f'ran {sim.steps} steps, halted: {h}')
    print(f'  ack 0x01C6 = {sim.xdata[0x01C6]:02X}   pending 0x01CC = {sim.xdata[0x01CC]:02X}   state 0x8B51 = {sim.xdata[0x8B51]:02X}')
    print(f'  id raw 0x8AD0 = {sim.xdata[0x8AD0]:02X}  norm 0x8AD1 = {sim.xdata[0x8AD1]:02X}  param 0x8AD2-6 = {sim.xdata[0x8AD2:0x8AD7].hex()}')
    if sim.rom_calls:
        print('  ROM calls: ' + ' '.join(f'{a:04X}x{n}' for a, n in
                                         sorted(sim.rom_calls.items())))
    if sim.reg_writes:
        print('  reg writes (last 10): ' + ' '.join(
            f'{a:04X}<={v:02X}' for s, a, v in sim.reg_writes[-10:]))


if __name__ == '__main__':
    main()
