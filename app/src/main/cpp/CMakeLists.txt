cmake_minimum_required(VERSION 3.22)
project(pybox_native)

# llama.cpp source is checked out here by the CI workflow
# (.github/workflows/build.yml) before Gradle runs, via:
#   git clone --depth 1 https://github.com/ggml-org/llama.cpp app/src/main/cpp/llama.cpp
# It is NOT vendored in this repo to keep it small and stay on upstream fixes.
set(LLAMA_DIR "${CMAKE_CURRENT_SOURCE_DIR}/llama.cpp")

if(NOT EXISTS "${LLAMA_DIR}/CMakeLists.txt")
    message(FATAL_ERROR
        "llama.cpp source not found at ${LLAMA_DIR}. "
        "The CI workflow clones it before building — if you're building "
        "locally with Android Studio, clone it yourself first:\n"
        "  git clone --depth 1 https://github.com/ggml-org/llama.cpp ${LLAMA_DIR}")
endif()

# We only need the API server binary, not the web UI, tests, or HTTPS support
# (the server is bound to 127.0.0.1 only and reached from inside the app).
set(LLAMA_STANDALONE OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_COMMON ON CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TOOLS ON CACHE BOOL "" FORCE)
set(LLAMA_BUILD_SERVER ON CACHE BOOL "" FORCE)
set(LLAMA_BUILD_EXAMPLES OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_TESTS OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_APP OFF CACHE BOOL "" FORCE)
set(LLAMA_BUILD_UI OFF CACHE BOOL "" FORCE)
set(LLAMA_USE_PREBUILT_UI OFF CACHE BOOL "" FORCE)
set(LLAMA_OPENSSL OFF CACHE BOOL "" FORCE)
set(LLAMA_CURL OFF CACHE BOOL "" FORCE)
set(GGML_LLAMAFILE OFF CACHE BOOL "" FORCE)
set(BUILD_SHARED_LIBS OFF CACHE BOOL "" FORCE)

# Android NDK targets ARMv8.2 baseline; dot-prod/i8mm speed up quantized
# inference on newer chips (S22 Ultra's Snapdragon 8 Gen 1 supports both).
if(ANDROID_ABI STREQUAL "arm64-v8a")
    set(CMAKE_C_FLAGS "${CMAKE_C_FLAGS} -march=armv8.2-a+dotprod+i8mm")
    set(CMAKE_CXX_FLAGS "${CMAKE_CXX_FLAGS} -march=armv8.2-a+dotprod+i8mm")
endif()

add_subdirectory(${LLAMA_DIR} llama_build)

# llama-server is a standalone executable, not a JNI library. Android will
# only extract/execute files that live under jniLibs and follow the
# lib*.so naming convention, so we rename+copy it there post-build.
add_custom_command(TARGET llama-server POST_BUILD
    COMMAND ${CMAKE_COMMAND} -E make_directory
        "${CMAKE_CURRENT_SOURCE_DIR}/../jniLibs/${ANDROID_ABI}"
    COMMAND ${CMAKE_COMMAND} -E copy
        "$<TARGET_FILE:llama-server>"
        "${CMAKE_CURRENT_SOURCE_DIR}/../jniLibs/${ANDROID_ABI}/libllama_server.so"
    COMMENT "Staging llama-server as libllama_server.so for packaging"
)
