"""Show what a plugged-in HID game controller says it has (Windows only, stdlib only).

Usage:  python python/hid_caps.py 20D6 A710        (vendor id, product id; hex)

For each matching HID device it prints the product name, its top-level usage (Joystick 0x04 or Gamepad 0x05:
Linux's hid-generic numbers buttons differently for the two), the button usages, and the value usages (axes,
hat switch). That's enough to work out how Firefox on a TV box will number the buttons, without the TV box.
"""
import ctypes
import ctypes.wintypes as wt
import sys

hid = ctypes.WinDLL("hid.dll")                                  # Windows' HID class driver API
setupapi = ctypes.WinDLL("setupapi.dll")                        # to list the HID devices
kernel32 = ctypes.WinDLL("kernel32.dll", use_last_error=True)


class GUID(ctypes.Structure):
    _fields_ = [("d1", wt.DWORD), ("d2", wt.WORD), ("d3", wt.WORD), ("d4", ctypes.c_ubyte * 8)]


class SP_DEVICE_INTERFACE_DATA(ctypes.Structure):
    _fields_ = [("cbSize", wt.DWORD), ("guid", GUID), ("flags", wt.DWORD), ("reserved", ctypes.c_void_p)]


class HIDP_CAPS(ctypes.Structure):
    _fields_ = [("Usage", wt.USHORT), ("UsagePage", wt.USHORT), ("InputReportByteLength", wt.USHORT),
                ("OutputReportByteLength", wt.USHORT), ("FeatureReportByteLength", wt.USHORT),
                ("Reserved", wt.USHORT * 17), ("NumberLinkCollectionNodes", wt.USHORT),
                ("NumberInputButtonCaps", wt.USHORT), ("NumberInputValueCaps", wt.USHORT),
                ("NumberInputDataIndices", wt.USHORT), ("NumberOutputButtonCaps", wt.USHORT),
                ("NumberOutputValueCaps", wt.USHORT), ("NumberOutputDataIndices", wt.USHORT),
                ("NumberFeatureButtonCaps", wt.USHORT), ("NumberFeatureValueCaps", wt.USHORT),
                ("NumberFeatureDataIndices", wt.USHORT)]


class HIDP_CAP(ctypes.Structure):
    """HIDP_BUTTON_CAPS and HIDP_VALUE_CAPS share this 72-byte head: usage page, report id, and (for a range)
    UsageMin/UsageMax at offset 56, or (not a range) Usage at 56. Values also carry LogicalMin/Max at 36/40."""
    _fields_ = [("UsagePage", wt.USHORT), ("ReportID", ctypes.c_ubyte), ("IsAlias", ctypes.c_ubyte),
                ("BitField", wt.USHORT), ("LinkCollection", wt.USHORT), ("LinkUsage", wt.USHORT),
                ("LinkUsagePage", wt.USHORT), ("IsRange", ctypes.c_ubyte), ("IsStringRange", ctypes.c_ubyte),
                ("IsDesignatorRange", ctypes.c_ubyte), ("IsAbsolute", ctypes.c_ubyte),
                ("HasNull", ctypes.c_ubyte), ("Reserved", ctypes.c_ubyte), ("BitSize", wt.USHORT),
                ("ReportCount", wt.USHORT), ("Reserved2", wt.USHORT * 5), ("UnitsExp", wt.ULONG), ("Units", wt.ULONG),
                ("LogicalMin", ctypes.c_long), ("LogicalMax", ctypes.c_long), ("PhysicalMin", ctypes.c_long),
                ("PhysicalMax", ctypes.c_long), ("UsageMin", wt.USHORT), ("UsageMax", wt.USHORT),
                ("rest", ctypes.c_ubyte * 12)]


DESKTOP = {0x30: "X", 0x31: "Y", 0x32: "Z", 0x33: "Rx", 0x34: "Ry", 0x35: "Rz", 0x36: "Slider", 0x39: "Hat switch"}


def device_paths():
    """Every HID device interface path (\\\\?\\hid#vid_...)."""
    guid = GUID()
    hid.HidD_GetHidGuid(ctypes.byref(guid))                     # the HID interface class
    setupapi.SetupDiGetClassDevsW.restype = ctypes.c_void_p     # a 64-bit handle: must be set before the call
    setupapi.SetupDiEnumDeviceInterfaces.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, wt.DWORD, ctypes.c_void_p]
    setupapi.SetupDiGetDeviceInterfaceDetailW.argtypes = [ctypes.c_void_p] * 6
    info = setupapi.SetupDiGetClassDevsW(ctypes.byref(guid), None, None, 0x12)   # PRESENT | DEVICEINTERFACE
    i = 0
    while True:
        data = SP_DEVICE_INTERFACE_DATA(cbSize=ctypes.sizeof(SP_DEVICE_INTERFACE_DATA))
        if not setupapi.SetupDiEnumDeviceInterfaces(info, None, ctypes.byref(guid), i, ctypes.byref(data)):
            break                                               # no more devices
        size = wt.DWORD()
        setupapi.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(data), None, 0, ctypes.byref(size), None)
        buf = ctypes.create_string_buffer(size.value)
        ctypes.cast(buf, ctypes.POINTER(wt.DWORD))[0] = 8 if ctypes.sizeof(ctypes.c_void_p) == 8 else 6   # cbSize
        if setupapi.SetupDiGetDeviceInterfaceDetailW(info, ctypes.byref(data), buf, size.value, None, None):
            yield ctypes.wstring_at(ctypes.addressof(buf) + 4)  # DevicePath follows the DWORD cbSize
        i += 1


def caps_of(path):
    """Print one device's name, collection usage, buttons and values."""
    kernel32.CreateFileW.restype = wt.HANDLE
    kernel32.CreateFileW.argtypes = [wt.LPCWSTR, wt.DWORD, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD, wt.HANDLE]
    for fn in (hid.HidD_GetProductString, hid.HidD_GetManufacturerString, hid.HidD_GetPreparsedData):
        fn.argtypes = [wt.HANDLE, ctypes.c_void_p, wt.ULONG] if fn is not hid.HidD_GetPreparsedData else [wt.HANDLE, ctypes.c_void_p]
    hid.HidP_GetCaps.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    hid.HidP_GetButtonCaps.argtypes = hid.HidP_GetValueCaps.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p]
    hid.HidD_FreePreparsedData.argtypes = [ctypes.c_void_p]
    kernel32.CloseHandle.argtypes = [wt.HANDLE]
    handle = kernel32.CreateFileW(path, 0, 3, None, 3, 0, None)   # no access needed for caps; share read/write
    if handle in (None, wt.HANDLE(-1).value):
        print("  can't open:", path)
        return
    try:
        name = ctypes.create_unicode_buffer(256)
        hid.HidD_GetProductString(handle, name, ctypes.sizeof(name))
        maker = ctypes.create_unicode_buffer(256)
        hid.HidD_GetManufacturerString(handle, maker, ctypes.sizeof(maker))
        pre = ctypes.c_void_p()
        hid.HidD_GetPreparsedData(handle, ctypes.byref(pre))
        caps = HIDP_CAPS()
        hid.HidP_GetCaps(pre, ctypes.byref(caps))
        print(f"{path}\n  '{maker.value}' '{name.value}'  top usage page 0x{caps.UsagePage:02X} usage 0x{caps.Usage:02X}"
              f" ({'Gamepad' if caps.Usage == 5 else 'Joystick' if caps.Usage == 4 else '?'}), input report {caps.InputReportByteLength} bytes")
        for kind, count, fn in (("button", caps.NumberInputButtonCaps, hid.HidP_GetButtonCaps),
                                ("value", caps.NumberInputValueCaps, hid.HidP_GetValueCaps)):
            if not count:
                continue
            arr = (HIDP_CAP * count)()
            n = wt.USHORT(count)
            fn(0, arr, ctypes.byref(n), pre)                    # 0 = input reports
            for c in arr[: n.value]:
                lo, hi = (c.UsageMin, c.UsageMax) if c.IsRange else (c.UsageMin, c.UsageMin)
                label = ", ".join(DESKTOP.get(u, hex(u)) for u in range(lo, hi + 1)) if c.UsagePage == 1 else f"usages {lo}-{hi}"
                extra = f" logical {c.LogicalMin}..{c.LogicalMax}" if kind == "value" else ""
                print(f"  {kind}: page 0x{c.UsagePage:02X} report {c.ReportID} {label}{extra}")
        hid.HidD_FreePreparsedData(pre)
    finally:
        kernel32.CloseHandle(handle)


if __name__ == "__main__":
    vid, pid = (sys.argv[1:3] + ["", ""])[:2]
    want = f"vid_{vid.lower()}&pid_{pid.lower()}"
    for p in device_paths():
        if want in p.lower():
            caps_of(p)
