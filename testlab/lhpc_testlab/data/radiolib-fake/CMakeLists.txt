# Test-lab RadioLib stub: satisfies the manifest's cmake build (build/libRadioLib.a)
# so the daemon stack's build_requires edge stays real without the actual library.
cmake_minimum_required(VERSION 3.16)
project(RadioLibFake CXX)
add_library(RadioLib STATIC stub.cpp)
