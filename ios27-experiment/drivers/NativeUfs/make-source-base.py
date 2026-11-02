#!/usr/bin/env python3
# SPDX-License-Identifier: BSD-2-Clause-Patent
# Ported from UniXNU scripts/build-sheng-source-storage.py (407db1aefd4e4e50a944fcda73442bde566dbce1, lines 152-165), Copyright (c) 2026, UniXNU contributors.
"""Write the kernel's copy of the public IOBlockStorageDevice.cpp: the C++ class name stays, for the compiler's arm64e discriminators, and the
metaclass registers as UniXNUBlockStorageDevice under the original family's class, which SourceStorage.cpp resolves first."""
import argparse
from pathlib import Path

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('source', type=Path, help='IOBlockStorageDevice.cpp of the pinned IOStorageFamily')
parser.add_argument('output', type=Path)
args = parser.parse_args()
original = args.source.read_text()
macro = 'OSDefineMetaClassAndAbstractStructors(IOBlockStorageDevice,IOService)'
if original.count(macro) != 1:
    parser.error('Pinned IOBlockStorageDevice registration macro changed')
args.output.write_text(original.replace(macro, '''
extern "C" const OSMetaClass *native_source_storage_parent(void);
OSDefineMetaClassWithInit(IOBlockStorageDevice, IOService, )
IOBlockStorageDevice::MetaClass::MetaClass()
    : OSMetaClass("UniXNUBlockStorageDevice", native_source_storage_parent(), sizeof(IOBlockStorageDevice)) {}
OSDefineAbstractStructors(IOBlockStorageDevice, IOService)
OSDefineOperatorMethods(IOBlockStorageDevice)
'''))
