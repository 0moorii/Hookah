/* A real Windows child process for launcher argument and status verification. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

static void json_string(const wchar_t *value) {
    int size = WideCharToMultiByte(CP_UTF8, 0, value, -1, NULL, 0, NULL, NULL);
    char *utf8 = (char *)calloc((size_t)size, 1);
    WideCharToMultiByte(CP_UTF8, 0, value, -1, utf8, size, NULL, NULL);
    putchar('"');
    for (char *character = utf8; *character; ++character) {
        if (*character == '"' || *character == '\\') putchar('\\');
        if ((unsigned char)*character < 32) printf("\\u%04x", (unsigned char)*character);
        else putchar(*character);
    }
    putchar('"');
    free(utf8);
}

int wmain(int argc, wchar_t **argv) {
    wchar_t program[256];
    int status = 0;
    GetEnvironmentVariableW(L"HOOKAH_PROGRAM_NAME", program, 256);
    printf("{\"program\":");
    json_string(program);
    printf(",\"argv\":[");
    for (int index = 1; index < argc; ++index) {
        if (index != 1) putchar(',');
        json_string(argv[index]);
        if (wcscmp(argv[index], L"--probe-exit") == 0 && index + 1 < argc)
            status = _wtoi(argv[index + 1]);
    }
    printf("]}\n");
    return status;
}
