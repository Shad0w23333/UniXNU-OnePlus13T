#!/usr/bin/env python3
"""Offline tests of the command line's limit in payload.py, against what really takes the line on the phone: DarwinBoot's copy, compiled from its
own source when a C compiler is there, and XNU's boot_args.

    python3 scripts/unixnu/test_payload.py
"""
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from unixnu import payload  # noqa: E402
from unixnu.util import Error  # noqa: E402

TOP = Path(__file__).resolve().parents[3]
DARWINBOOT = TOP / 'UniXNU-Pagani-Drivers/drivers/PaganiDarwinBoot/DarwinBootPkg/Driver'
XNU_BOOT_H = TOP / 'UniXNU/out-gpu/feeds/xnu-b7545ced/pexpert/pexpert/arm64/boot.h'
TOML_C = TOP / 'UniXNU/package/UniXNUPkg/Library/UniXNULib/Toml.c'


def line_of(length):
    """Kernel arguments of the check's own alphabet that join to exactly this many bytes."""
    head = 'a=' + 'x' * (length - 2)
    return [head]


class Limit(unittest.TestCase):
    def test_the_limit_is_inside_the_loader(self):
        self.assertEqual(payload.KERNEL_ARGS_LIMIT, 1020)
        self.assertEqual(payload.DARWINBOOT_ARGS_MAX, 1023)
        self.assertLess(payload.KERNEL_ARGS_LIMIT, payload.DARWINBOOT_ARGS_MAX)

    def test_boundary(self):
        self.assertEqual(len(payload.check_args(line_of(payload.KERNEL_ARGS_LIMIT))), payload.KERNEL_ARGS_LIMIT)
        with self.assertRaisesRegex(Error, f'{payload.KERNEL_ARGS_LIMIT + 1} bytes; the limit is {payload.KERNEL_ARGS_LIMIT}'):
            payload.check_args(line_of(payload.KERNEL_ARGS_LIMIT + 1))

    def test_more_arguments_count_their_spaces(self):
        arguments = ['a=' + 'x' * 500, 'b=' + 'x' * 500, 'c=' + 'x' * 12]
        self.assertEqual(len(payload.check_args(arguments)), 1020)
        arguments[-1] += 'x'
        with self.assertRaises(Error):
            payload.check_args(arguments)

    def test_the_lines_of_the_last_round_pass(self):
        """996 and 998 bytes, the two images of 2026-11-01: what the old limit of 1000 was close to refusing."""
        self.assertEqual(len(payload.check_args(line_of(996))), 996)
        self.assertEqual(len(payload.check_args(line_of(998))), 998)

    def test_characters_are_still_checked(self):
        for bad in ('a="b"', 'a b', 'a=\\', 'é=1'):
            with self.assertRaises(Error):
                payload.check_args([bad])

    def test_a_confirmed_choice_cannot_pass_the_loader(self):
        line = 'x' * payload.KERNEL_ARGS_LIMIT
        payload.check_confirm_args(line, ['a='])                          # 1020 + space + 2 = 1023
        with self.assertRaisesRegex(Error, 'over the 1023 bytes'):
            payload.check_confirm_args(line, ['a=1'])


class Consumers(unittest.TestCase):
    """The numbers above against the code that takes the line."""

    @unittest.skipUnless((DARWINBOOT / 'DarwinBoot.c').is_file(), 'no DarwinBoot source')
    def test_darwinboot_source(self):
        source = (DARWINBOOT / 'DarwinBoot.c').read_text()
        self.assertIn('CHAR8                           CommandLine[1024];', source)
        self.assertIn('if ((Index >= sizeof (CommandLine) - 1) || (Request->BootArgs[Index] > 0x7f)) { return EFI_BAD_BUFFER_SIZE; }', source)
        self.assertIn('CHAR8   CommandLine[1024];', (DARWINBOOT / 'BootArgs.h').read_text())

    @unittest.skipUnless((DARWINBOOT / 'DarwinBoot.c').is_file() and shutil.which('cc'), 'no DarwinBoot source or compiler')
    def test_darwinboots_loop_takes_1023_and_refuses_1024(self):
        """DarwinBoot's own lines, cut from its source and compiled here."""
        source = (DARWINBOOT / 'DarwinBoot.c').read_text()
        begin = source.index('  ZeroMem (CommandLine, sizeof (CommandLine));')
        end = source.index('  Root = NULL;', begin)
        program = r'''
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
typedef char CHAR8; typedef unsigned short CHAR16; typedef unsigned long UINTN;
#define EFI_BAD_BUFFER_SIZE 4
#define NULL_REQUEST 0
#define ZeroMem(p, n) memset(p, 0, n)
struct request { CHAR16 *BootArgs; };
static int take(struct request *Request, char *copy, UINTN *copied)
{
  CHAR8 CommandLine[1024];
  UINTN Index;
''' + source[begin:end] + r'''
  memcpy(copy, CommandLine, sizeof(CommandLine));
  *copied = strlen(CommandLine);
  return 0;
}
int main(int argc, char **argv)
{
  for (int length = 1; length < argc; length++) {
    int n = atoi(argv[length]);
    static CHAR16 args[4096];
    static char copy[1024];
    UINTN copied = 0;
    for (int i = 0; i < n; i++) args[i] = 'x';
    args[n] = 0;
    struct request request = { args };
    int status = take(&request, copy, &copied);
    printf("%d %d %lu\n", n, status, copied);
  }
  return 0;
}
'''
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            (directory / 'main.c').write_text(program)
            subprocess.run(['cc', '-Wno-everything', str(directory / 'main.c'), '-o', str(directory / 'run')], check=True, capture_output=True, text=True)
            output = subprocess.run([str(directory / 'run'), '998', str(payload.KERNEL_ARGS_LIMIT), '1023', '1024', '2000'],
                                    capture_output=True, text=True, check=True).stdout
        rows = [line.split() for line in output.splitlines()]
        self.assertEqual(rows, [['998', '0', '998'], ['1020', '0', '1020'], ['1023', '0', '1023'], ['1024', '4', '0'], ['2000', '4', '0']])

    @unittest.skipUnless(XNU_BOOT_H.is_file(), 'no XNU source')
    def test_xnu(self):
        self.assertEqual(int(re.search(r'#define BOOT_LINE_LENGTH\s+(\d+)', XNU_BOOT_H.read_text()).group(1)), 1024)

    @unittest.skipUnless(TOML_C.is_file(), 'no UniXNU package source')
    def test_the_configuration_parser_grows_to_size(self):
        """args = "..." in unixnu.toml: ParseString doubles its buffer, there is no line limit to meet before DarwinBoot."""
        source = TOML_C.read_text()
        body = source[source.index('ParseString ('):]
        body = body[:body.index('\n}\n')]
        self.assertIn('Cap   = 32;', body)
        self.assertRegex(body, r'Cap\s*\*=\s*2|Cap\s*=\s*Cap\s*\*\s*2|ReallocatePool')


if __name__ == '__main__':
    unittest.main()
