#!/bin/bash
set -e
export WORKSPACE=/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/6b6af18b-a469-4fef-b255-20e990fdd62a/scratchpad/w/edk/ws
EDK2=/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/6b6af18b-a469-4fef-b255-20e990fdd62a/scratchpad/w/edk/mu
export PACKAGES_PATH=${DBDIR:-/Users/xiaochoumao/unixnu-13t/ios27-experiment/drivers/PaganiDarwinBoot}:/Users/xiaochoumao/unixnu-13t/UniXNU/package:$EDK2
export EDK_TOOLS_PATH=$EDK2/BaseTools
export CONF_PATH=/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/6b6af18b-a469-4fef-b255-20e990fdd62a/scratchpad/w/edk/Conf
export CLANG_BIN=/private/tmp/claude-501/-Users-xiaochoumao-unixnu-13t/6b6af18b-a469-4fef-b255-20e990fdd62a/scratchpad/w/edk/toolchain/
export PYTHON_COMMAND=python3
export PYTHONDONTWRITEBYTECODE=1
export PATH=$EDK_TOOLS_PATH/BinWrappers/PosixLike:$EDK_TOOLS_PATH/Source/C/bin:$PATH
cd $WORKSPACE
build -p DarwinBootPkg/DarwinBoot.dsc -a AARCH64 -b DEBUG -t CLANGPDB -n 6
