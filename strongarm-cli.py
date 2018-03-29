# -*- coding: utf-8 -*-

import argparse

from strongarm.debug_util import DebugUtil
from strongarm.macho import \
    MachoParser, \
    MachoBinary, \
    MachoAnalyzer
from strongarm.cli.utils import \
    pick_macho_slice, \
    disassemble_method, \
    print_binary_info, \
    print_binary_load_commands, \
    print_binary_segments, \
    print_binary_sections, \
    print_analyzer_imported_symbols, \
    print_analyzer_exported_symbols, \
    print_analyzer_methods, \
    print_analyzer_classes, \
    print_analyzer_protocols, \
    print_selector


def print_header(args) -> None:
    header_lines = [
        '\nstrongarm - Mach-O analyzer',
        '{}'.format(args.binary_path),
    ]
    # find longest line
    longest_line_len = 0
    for line in header_lines:
        longest_line_len = max(longest_line_len, len(line))
    # add a line of hyphens, where the hyphen count matches the longest line
    header_lines.append('-' * longest_line_len)
    header_lines.append('')

    # print header
    for line in header_lines:
        print(line)


from typing import Text


class InfoCommand:
    def __init__(self, binary: MachoBinary, analyzer: MachoAnalyzer):
        self.binary = binary
        self.analyzer = analyzer

        self.commands = {
            'all': (self.run_all_commands, None),
            'metadata': (print_binary_info, self.binary),
            'segments': (print_binary_segments, self.binary),
            'sections': (print_binary_sections, self.binary),
            'loads': (print_binary_load_commands, self.binary),
            'classes': (print_analyzer_classes, self.analyzer),
            'protocols': (print_analyzer_protocols, self.analyzer),
            'methods': (print_analyzer_methods, self.analyzer),
            'imports': (print_analyzer_imported_symbols, self.analyzer),
            'exports': (print_analyzer_exported_symbols, self.analyzer),
        }

    def description(self):
        rep = 'Read binary information. info '
        for cmd in self.commands.keys():
            rep += f'[{cmd}] '
        return rep

    def run_all_commands(self):
        for cmd in self.commands.keys():
            if cmd == 'all':
                continue
            self.run_command(cmd)

    def run_command(self, cmd: Text):
        if cmd == 'all':
            self.run_all_commands()
            return

        if cmd not in self.commands:
            print(f'Unknown argument supplied to info: {cmd}')
            return
        func, arg = self.commands[cmd]
        func(arg)


class StrongarmShell:
    def __init__(self, binary: MachoBinary, analyzer: MachoAnalyzer):
        self.binary = binary
        self.analyzer = analyzer

        self.commands = {
            'help': ('Display available commands', self.help),
            'exit': ('Exit interactive shell', self.exit),
            'info': (InfoCommand(self.binary, self.analyzer).description(), self.info),
            'sels': ('List selectors implemented by a class. sels [class]', self.selectors),
            'disasm': ('Decompile a given selector. disasm [sel]', self.disasm),
        }
        print('strongarm interactive shell\nType \'help\' for available commands.')
        self.active = True

    def selectors(self, args):
        if not len(args):
            print('Usage: sels [class]')
            return

        class_name = args[0]
        objc_classes = [x for x in self.analyzer.objc_classes() if x.name == class_name]
        if not len(objc_classes):
            print(f"Unknown class '{class_name}'. Run 'info classes' for a list of implemented classes.")
            return

        objc_class = objc_classes[0]
        for sel in objc_class.selectors:
            print_selector(objc_class, sel)

    def disasm(self, args):
        if not len(args):
            print('Usage: disasm [sel]')
            return

        sel_name = args[0]
        matching_sels = [x for x in self.analyzer.get_objc_methods() if x.objc_sel.name == sel_name]
        if not len(matching_sels):
            print(f"Unknown selector '{sel_name}'. Run 'info methods' for a list of selectors.")
            return

        disassembled_str = disassemble_method(self.binary, matching_sels[0])
        print(disassembled_str)

    def help(self, args):
        print('Commands\n'
              '----------------')
        for name, (description, funcptr) in self.commands.items():
            print(f'{name}: {description}')

    def info(self, args):
        info_cmd = InfoCommand(self.binary, self.analyzer)
        if not len(args):
            print('No option provided')
            print(info_cmd.description())
        for option in args:
            info_cmd.run_command(option)

    def exit(self, args):
        print('Quitting...')
        self.active = False

    def run_command(self, user_input: Text):
        components = user_input.split(' ')
        cmd_name = components[0]
        cmd_args = components[1:]

        if cmd_name not in self.commands:
            print(f'Unknown command: \'{cmd_name}\'. Type \'help\' for available commands.')
            return self.active

        func = self.commands[cmd_name][1]
        func(cmd_args)
        return self.active

    def process_command(self):
        user_input = input('strongarm$ ')
        return self.run_command(user_input)


def main():
    parser = argparse.ArgumentParser(description='Mach-O Analyzer')
    parser.add_argument(
        '--verbose', action='store_true', help=
        'Output extra info while analyzing'
    )
    parser.add_argument(
        'binary_path', metavar='binary_path', type=str, help=
        'Path to binary to analyze'
    )
    args = parser.parse_args()

    if args.verbose:
        DebugUtil.debug = True

    print_header(args)

    parser = MachoParser(args.binary_path)

    # print slice info
    print('Slices:')
    for macho_slice in parser.slices:
        print('\t{} Mach-O slice @ {}'.format(macho_slice.cpu_type.name, hex(macho_slice._offset_within_fat)))

    binary = pick_macho_slice(parser)
    print('Reading {} slice\n\n'.format(binary.cpu_type.name))

    analyzer = MachoAnalyzer.get_analyzer(binary)
    shell = StrongarmShell(binary, analyzer)

    autorun_cmd = 'info metadata segments sections loads'
    print(f'Auto-running \'{autorun_cmd}\'\n\n')
    shell.run_command(autorun_cmd)

    # this will return False once the shell exists
    while shell.process_command():
        pass
    print('May your arms be beefy and your binaries unencrypted')


if __name__ == '__main__':
    main()
