/* Native Windows console entrypoint. All application files live in .hookah. */
#define WIN32_LEAN_AND_MEAN
#include <windows.h>
#include <stdio.h>
#include <stdlib.h>
#include <wchar.h>

#ifndef HOOKAH_COMPONENT
#error HOOKAH_COMPONENT must be a wide string literal
#endif
#ifndef HOOKAH_VERSION
#error HOOKAH_VERSION must be a wide string literal
#endif

#define CAPACITY 32768

static void message(HANDLE stream, const wchar_t *text) {
    DWORD mode, written;
    if (GetConsoleMode(stream, &mode)) {
        WriteConsoleW(stream, text, (DWORD)wcslen(text), &written, NULL);
    } else {
        int length = WideCharToMultiByte(CP_UTF8, 0, text, -1, NULL, 0, NULL, NULL);
        char *utf8 = (char *)calloc((size_t)length, 1);
        if (utf8) {
            WideCharToMultiByte(CP_UTF8, 0, text, -1, utf8, length, NULL, NULL);
            WriteFile(stream, utf8, (DWORD)(length - 1), &written, NULL);
            free(utf8);
        }
    }
}

static int join_path(wchar_t *destination, const wchar_t *root, const wchar_t *relative) {
    if (wcslen(root) + wcslen(relative) + 2 >= CAPACITY) return 0;
    wcscpy(destination, root);
    wcscat(destination, L"\\");
    wcscat(destination, relative);
    return 1;
}

static int character(wchar_t *command, size_t *length, wchar_t value) {
    if (*length + 1 >= CAPACITY) return 0;
    command[(*length)++] = value;
    command[*length] = 0;
    return 1;
}

/* Windows CRT quoting: quotes and terminal backslashes must be escaped. */
static int argument(wchar_t *command, size_t *length, const wchar_t *value) {
    size_t slashes = 0;
    if (*length && !character(command, length, L' ')) return 0;
    if (!character(command, length, L'"')) return 0;
    for (; *value; ++value) {
        if (*value == L'\\') {
            ++slashes;
            continue;
        }
        size_t count = *value == L'"' ? slashes * 2 + 1 : slashes;
        for (size_t index = 0; index < count; ++index)
            if (!character(command, length, L'\\')) return 0;
        slashes = 0;
        if (!character(command, length, *value)) return 0;
    }
    for (size_t index = 0; index < slashes * 2; ++index)
        if (!character(command, length, L'\\')) return 0;
    return character(command, length, L'"');
}

static BOOL WINAPI parent_control_handler(DWORD event) {
    /* The child receives console events too; wait for it to finish cleanup. */
    return event == CTRL_C_EVENT || event == CTRL_BREAK_EVENT;
}

static DWORD run_python(const wchar_t *python, int use_py_launcher,
                        const wchar_t *bootstrap, int argc, wchar_t **argv, int *started) {
    wchar_t *command = (wchar_t *)calloc(CAPACITY, sizeof(wchar_t));
    size_t length = 0;
    STARTUPINFOW startup;
    PROCESS_INFORMATION process;
    DWORD status = 1;
    *started = 0;
    if (!command) return status;
    if (!argument(command, &length, python)
        || (use_py_launcher && !argument(command, &length, L"-3"))
        || !argument(command, &length, L"-B")
        || !argument(command, &length, bootstrap)) goto too_long;
    for (int index = 1; index < argc; ++index)
        if (!argument(command, &length, argv[index])) goto too_long;
    ZeroMemory(&startup, sizeof(startup));
    ZeroMemory(&process, sizeof(process));
    startup.cb = sizeof(startup);
    if (!CreateProcessW(NULL, command, NULL, NULL, TRUE, 0, NULL, NULL, &startup, &process)) {
        free(command);
        return status;
    }
    *started = 1;
    CloseHandle(process.hThread);
    if (WaitForSingleObject(process.hProcess, INFINITE) == WAIT_OBJECT_0)
        GetExitCodeProcess(process.hProcess, &status);
    CloseHandle(process.hProcess);
    free(command);
    return status;
too_long:
    message(GetStdHandle(STD_ERROR_HANDLE), L"Hookah: the command line is too long for Windows.\n");
    free(command);
    *started = 1;
    return status;
}

static int show_help(const wchar_t *root) {
    wchar_t path[CAPACITY];
    if (!join_path(path, root, L"HELP.md")) return 1;
    HANDLE file = CreateFileW(path, GENERIC_READ, FILE_SHARE_READ, NULL, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, NULL);
    if (file == INVALID_HANDLE_VALUE) {
        message(GetStdHandle(STD_ERROR_HANDLE), L"Hookah: HELP.md is missing. Extract the complete ZIP first.\n");
        return 1;
    }
    DWORD size = GetFileSize(file, NULL), read;
    if (size == INVALID_FILE_SIZE || size > 1024 * 1024) {
        CloseHandle(file);
        return 1;
    }
    char *data = (char *)calloc((size_t)size + 1, 1);
    if (!data || !ReadFile(file, data, size, &read, NULL)) {
        CloseHandle(file);
        free(data);
        return 1;
    }
    CloseHandle(file);
    int count = MultiByteToWideChar(CP_UTF8, 0, data, (int)read, NULL, 0);
    wchar_t *text = (wchar_t *)calloc((size_t)count + 1, sizeof(wchar_t));
    if (text) {
        MultiByteToWideChar(CP_UTF8, 0, data, (int)read, text, count);
        message(GetStdHandle(STD_OUTPUT_HANDLE), text);
    }
    free(data);
    free(text);
    return 0;
}

int wmain(int argc, wchar_t **argv) {
    wchar_t root[CAPACITY], bootstrap[CAPACITY], python[CAPACITY], support[CAPACITY];
    DWORD length = GetModuleFileNameW(NULL, root, CAPACITY);
    if (!length || length >= CAPACITY) return 1;
    wchar_t *separator = wcsrchr(root, L'\\');
    if (!separator) return 1;
    SetEnvironmentVariableW(L"HOOKAH_PROGRAM_NAME", separator + 1);
    *separator = 0;
    SetEnvironmentVariableW(L"HOOKAH_PACKAGE_ROOT", root);
    SetEnvironmentVariableW(L"PYTHONUTF8", L"1");
    SetEnvironmentVariableW(L"PYTHONIOENCODING", L"utf-8");
    SetEnvironmentVariableW(L"PYTHONDONTWRITEBYTECODE", L"1");
    SetConsoleCtrlHandler(parent_control_handler, TRUE);
    if (join_path(support, root, L".hookah")) {
        DWORD attributes = GetFileAttributesW(support);
        if (attributes != INVALID_FILE_ATTRIBUTES)
            SetFileAttributesW(support, attributes | FILE_ATTRIBUTE_HIDDEN);
    }
    if (argc == 2 && wcscmp(argv[1], L"--version") == 0) {
        message(GetStdHandle(STD_OUTPUT_HANDLE), L"Hookah " HOOKAH_COMPONENT L" " HOOKAH_VERSION L"\n");
        return 0;
    }
    if (argc == 2 && (wcscmp(argv[1], L"--help") == 0 || wcscmp(argv[1], L"-h") == 0 || wcscmp(argv[1], L"help") == 0))
        return show_help(root);
    if (!join_path(bootstrap, root, L".hookah\\launcher.py") || GetFileAttributesW(bootstrap) == INVALID_FILE_ATTRIBUTES) {
        message(GetStdHandle(STD_ERROR_HANDLE), L"Hookah: the support folder is missing. Extract the complete ZIP first.\n");
        return 1;
    }
    int started;
    DWORD status;
    DWORD selected = GetEnvironmentVariableW(L"HOOKAH_PYTHON", python, CAPACITY);
    if (selected) {
        if (selected >= CAPACITY) return 1;
        status = run_python(python, 0, bootstrap, argc, argv, &started);
        if (started) return (int)status;
        message(GetStdHandle(STD_ERROR_HANDLE), L"Hookah: HOOKAH_PYTHON could not be started. Check its executable path.\n");
        return 127;
    }
    if (join_path(python, root, L".hookah\\environment\\Scripts\\python.exe") && GetFileAttributesW(python) != INVALID_FILE_ATTRIBUTES) {
        status = run_python(python, 0, bootstrap, argc, argv, &started);
        if (started) return (int)status;
        message(GetStdHandle(STD_ERROR_HANDLE), L"Hookah: the installed environment could not be started. Run setup again.\n");
        return 1;
    }
    status = run_python(L"py.exe", 1, bootstrap, argc, argv, &started);
    if (started) return (int)status;
    status = run_python(L"python.exe", 0, bootstrap, argc, argv, &started);
    if (started) return (int)status;
    message(GetStdHandle(STD_ERROR_HANDLE), L"Hookah: Python 3.10+ was not found. Install Python, then run this executable with setup.\n");
    return 127;
}
