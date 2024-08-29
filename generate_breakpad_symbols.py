#!/usr/bin/env python
# Copyright 2013 The Chromium Authors. All rights reserved.
# Copyright 2024 LunarG, Inc.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""A tool to generate symbols for a binary suitable for breakpad.
Currently, the tool only supports Linux and Android. Support for other
platforms is planned.

This is a modified version of:
https://chromium.googlesource.com/chromium/src/+/master/components/crash/content/tools/generate_breakpad_symbols.py
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

def GetCommandOutput(command):
    """Runs the command list, returning its output.
    Prints the given command (which should be a list of one or more strings),
    then runs it and returns its output (stdout) as a string.
    From chromium_utils.
    """
    out = subprocess.run(command, capture_output = True, encoding = 'utf-8', check=True)
    return out.stdout

def FindLib(lib, rpaths):
    """Resolves the given library relative to a list of rpaths."""
    if lib.find('@rpath') == -1:
        return lib
    for rpath in rpaths:
        real_lib = re.sub('@rpath', rpath, lib)
        if os.access(real_lib, os.X_OK):
            return real_lib
    print(f'Could not find "{lib}"')
    return None

def GetSharedLibraryDependencies(binary):
    """Return absolute paths to all shared library dependecies of the binary."""
    ldd = GetCommandOutput(['ldd', binary])
    lib_re = re.compile(r'^\t.* => (.+) \(.*\)$')
    result = []
    for line in ldd.splitlines():
        m = lib_re.match(line)
        if m:
            result.append(m.group(1))
    return result

def GetDebugFile(binary):
    """Get the ubuntu debug symbol file for a given binary"""
    unstrip = GetCommandOutput(['eu-unstrip', '-n', '-e', binary]).rstrip().split(' ')
    dbg_file = unstrip[3]
    # it looks like '-' is supposed to be the value for 'no debug info exists', but
    # libpthread.so returns '.' which exists but is wrong.
    return dbg_file if dbg_file not in ('-','.') else None

def GetSoName(binary):
    """
    Get the SONAME field for a library. Mesa DRI drivers use the same SONAME
    for multiple drivers. Minidump files will reference the SONAME so we need
    to write symbols as if that was the file name. It is still a manual process
    to figure out which DRI driver is in use and add it to the cmd line
    arguments.
    """
    out = GetCommandOutput(['readelf', '-d', binary])
    # 0x000000000000000e (SONAME)             Library soname: [libgallium_dri.so]

    so_re = re.compile(r'.*\(SONAME\) *Library soname: \[([\w.]+)\]')
    for line in out.splitlines():
        m = so_re.match(line)
        if m:
            return m.group(1)
    return os.path.basename(binary)

def GenerateSymbols(symbol_dir, binary):
    """Dumps the symbols of binary and places them in the given directory."""
    dump_cmd =['dump_syms', '-v']
    # mesa drivers have a different soname, which what is shows up in the breakpad .dmp file
    # so we'll use it instead of the filename.
    soname = GetSoName(binary)
    if soname != os.path.basename(binary):
        print(f'different soname: {soname}')
        dump_cmd += ['-n', soname]

    # options must come before positional arguments
    dump_cmd.append(binary)

    # dump_syms will fail if we pass a debug directory but the debug file isn't found in it.
    dbg_file = GetDebugFile(binary)
    if dbg_file is not None and os.path.exists(dbg_file):
        print(f'debug_file: {dbg_file}')
        dump_cmd += [os.path.dirname(dbg_file)]

    syms = GetCommandOutput(dump_cmd)
    module_line = re.match('^MODULE [^ ]+ [^ ]+ ([0-9A-F]+) (.*)\n', syms)
    if soname != os.path.basename(binary):
        print(f'{module_line.group(2)} {module_line.group(1)}')
    output_path = os.path.join(symbol_dir, module_line.group(2), module_line.group(1))
    os.makedirs(output_path, exist_ok=True)
    symbol_file = module_line.group(2) + ".sym"
    with open(os.path.join(output_path, symbol_file), 'w', encoding='utf-8') as f:
        f.write(syms)

def main():
    if not sys.platform.startswith('linux'):
        print("Currently only supported on Linux.")
        return 1
    parser = argparse.ArgumentParser()
    parser.add_argument('-s', '--symbols-dir', default='./symbols',
                        help='The directory where to write the symbols file.')
    parser.add_argument('-c', '--clear', default=False, action='store_true',
                        help='Clear the symbols directory before writing new symbols.')
    parser.add_argument('binaries', nargs='+', help='list of binaries to process')
    args = parser.parse_args()

    if args.clear:
        shutil.rmtree(args.symbols_dir, ignore_errors=True)

    # Build the transitive closure of all dependencies.
    binaries = set(args.binaries)
    queue = args.binaries
    while queue:
        deps = GetSharedLibraryDependencies(queue.pop(0))
        new_deps = set(deps) - binaries
        binaries |= new_deps
        queue.extend(list(new_deps))
    for binary in binaries:
        print(f'binary: {binary}')
        GenerateSymbols(args.symbols_dir, binary)
        print('')
    return 0

if '__main__' == __name__:
    sys.exit(main())
