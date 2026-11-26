using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace Mnemerynne.Drive;

/// <summary>
/// The parts of Windows' Cloud Files API (cldapi.dll, cfapi.h) the drive uses, declared by hand so every struct
/// layout is visible here. Layouts are for 64-bit Windows (the app is built x64 only). Offsets in the comments
/// are bytes from the start of each struct; they match cfapi.h with its natural alignment.
/// Reference: https://learn.microsoft.com/windows/win32/cfapi/cloud-files-api-portal
/// </summary>
internal static unsafe class Cf
{
    private const string Dll = "cldapi.dll";

    // --------------------------------------------------------------------------------- callbacks
    /// <summary>CF_CALLBACK_TYPE: what Windows is asking or telling the provider.</summary>
    public enum CallbackType : uint
    {
        FetchData = 0,             // a program read a part of a placeholder that isn't downloaded: send the bytes
        CancelFetchData = 2,
        FetchPlaceholders = 3,     // a folder was opened for the first time: say what's in it
        CancelFetchPlaceholders = 4,
        NotifyDelete = 9,          // something is about to delete a placeholder: say yes or no
        NotifyRename = 11,         // something is about to rename or move a placeholder: say yes or no
        None = 0xffffffff,         // ends the callback table
    }

    /// <summary>CF_CALLBACK_INFO (what every callback gets). 64-bit layout.</summary>
    [StructLayout(LayoutKind.Sequential)]
    public struct CallbackInfo
    {
        public uint StructSize;             // 0
        public long ConnectionKey;          // 8   CF_CONNECTION_KEY
        public nint CallbackContext;        // 16
        public nint VolumeGuidName;         // 24  PCWSTR
        public nint VolumeDosName;          // 32  PCWSTR, e.g. "C:"
        public uint VolumeSerialNumber;     // 40
        public long SyncRootFileId;         // 48
        public nint SyncRootIdentity;       // 56
        public uint SyncRootIdentityLength; // 64
        public long FileId;                 // 72
        public long FileSize;               // 80
        public nint FileIdentity;           // 88  the bytes we gave the placeholder (DriveId)
        public uint FileIdentityLength;     // 96
        public nint NormalizedPath;         // 104 PCWSTR, e.g. "\Users\Edith\Mnemerynne\File box\a.txt" (no drive letter)
        public long TransferKey;            // 112
        public byte PriorityHint;           // 120
        public nint CorrelationVector;      // 128
        public nint ProcessInfo;            // 136
        public long RequestKey;             // 144
    }

    // CF_CALLBACK_PARAMETERS is { ULONG ParamSize; union { ... } } with the union at offset 8 (8-byte aligned).
    // The drive reads the few fields it needs by offset (see CloudProvider), which is clearer than C# unions:
    //   FetchData:          Flags @8 (uint), RequiredFileOffset @16, RequiredLength @24, OptionalFileOffset @32, OptionalLength @40
    //   FetchPlaceholders:  Flags @8, Pattern @16 (PCWSTR)
    //   Delete:             Flags @8 (CF_CALLBACK_DELETE_FLAG_IS_DIRECTORY = 1)
    //   Rename:             Flags @8 (CF_CALLBACK_RENAME_FLAG_IS_DIRECTORY = 1, SOURCE_IN_SCOPE = 2, TARGET_IN_SCOPE = 4), TargetPath @16 (PCWSTR)
    public const uint DeleteFlagIsDirectory = 1;
    public const uint RenameFlagIsDirectory = 1;
    public const uint RenameFlagTargetInScope = 4;

    [UnmanagedFunctionPointer(CallingConvention.StdCall)]
    public delegate void Callback(CallbackInfo* info, byte* parameters);

    /// <summary>CF_CALLBACK_REGISTRATION: one row of the callback table (16 bytes).</summary>
    [StructLayout(LayoutKind.Sequential)]
    public struct CallbackRegistration
    {
        public CallbackType Type;           // 0
        public nint Callback;               // 8  function pointer
    }

    // -------------------------------------------------------------------------------- operations
    public enum OperationType : uint
    {
        TransferData = 0,          // hand Windows a piece of a file's bytes
        TransferPlaceholders = 4,  // hand Windows a folder's contents
        AckDelete = 6,
        AckRename = 7,
    }

    /// <summary>CF_OPERATION_INFO (48 bytes).</summary>
    [StructLayout(LayoutKind.Sequential)]
    public struct OperationInfo
    {
        public uint StructSize;             // 0
        public OperationType Type;          // 4
        public long ConnectionKey;          // 8
        public long TransferKey;            // 16
        public nint CorrelationVector;      // 24
        public nint SyncStatus;             // 32
        public long RequestKey;             // 40
    }

    /// <summary>CF_OPERATION_PARAMETERS with the union members the drive uses, laid out explicitly.</summary>
    [StructLayout(LayoutKind.Explicit, Size = 48)]
    public struct OperationParameters
    {
        [FieldOffset(0)] public uint ParamSize;
        // TransferData
        [FieldOffset(8)] public uint TransferFlags;
        [FieldOffset(12)] public int CompletionStatus;       // NTSTATUS: 0 = success (shared by every member below)
        [FieldOffset(16)] public nint Buffer;
        [FieldOffset(24)] public long Offset;
        [FieldOffset(32)] public long Length;
        // TransferPlaceholders (same union, other names for the same bytes)
        [FieldOffset(16)] public long PlaceholderTotalCount;
        [FieldOffset(24)] public nint PlaceholderArray;
        [FieldOffset(32)] public uint PlaceholderCount;
        [FieldOffset(36)] public uint EntriesProcessed;
    }

    // ParamSize = offset of the union (8) + the size of the member used (CF_SIZE_OF_OP_PARAM in cfapi.h).
    public const uint SizeTransferData = 8 + 32;
    public const uint SizeTransferPlaceholders = 8 + 32;
    public const uint SizeAck = 8 + 8;                       // AckDelete / AckRename: Flags + CompletionStatus

    public const uint TransferPlaceholdersFlagDisableOnDemandPopulation = 2;   // "that's the whole folder"

    /// <summary>CF_FS_METADATA: FILE_BASIC_INFO (40 bytes with padding) + FileSize (48 bytes).</summary>
    [StructLayout(LayoutKind.Sequential)]
    public struct FsMetadata
    {
        public long CreationTime;           // 0   FILETIME as 100 ns since 1601 (UTC)
        public long LastAccessTime;         // 8
        public long LastWriteTime;          // 16
        public long ChangeTime;             // 24
        public uint FileAttributes;         // 32  FILE_ATTRIBUTE_DIRECTORY (0x10) / FILE_ATTRIBUTE_NORMAL (0x80)
        public uint Padding;                // 36
        public long FileSize;               // 40
    }

    /// <summary>CF_PLACEHOLDER_CREATE_INFO (88 bytes).</summary>
    [StructLayout(LayoutKind.Sequential)]
    public struct PlaceholderCreateInfo
    {
        public nint RelativeFileName;       // 0   PCWSTR: the name inside the folder
        public FsMetadata FsMetadata;       // 8
        public nint FileIdentity;           // 56
        public uint FileIdentityLength;     // 64
        public uint Flags;                  // 68  CF_PLACEHOLDER_CREATE_FLAG_*
        public int Result;                  // 72  HRESULT per entry
        public uint Padding;                // 76
        public long CreateUsn;              // 80
    }

    public const uint PlaceholderCreateFlagMarkInSync = 2;
    public const uint PlaceholderCreateFlagSupersede = 4;

    public const uint FileAttributeDirectory = 0x10;
    public const uint FileAttributeNormal = 0x80;

    // ------------------------------------------------------------------------------ registration
    /// <summary>CF_SYNC_REGISTRATION (72 bytes): used only when the WinRT registration isn't available.</summary>
    [StructLayout(LayoutKind.Sequential)]
    public struct SyncRegistration
    {
        public uint StructSize;             // 0
        public nint ProviderName;           // 8
        public nint ProviderVersion;        // 16
        public nint SyncRootIdentity;       // 24
        public uint SyncRootIdentityLength; // 32
        public nint FileIdentity;           // 40
        public uint FileIdentityLength;     // 48
        public Guid ProviderId;             // 52
    }

    /// <summary>CF_SYNC_POLICIES (24 bytes).</summary>
    [StructLayout(LayoutKind.Sequential)]
    public struct SyncPolicies
    {
        public uint StructSize;             // 0
        public ushort HydrationPrimary;     // 4   CF_HYDRATION_POLICY_FULL = 2: a file is downloaded whole when opened
        public ushort HydrationModifier;    // 6
        public ushort PopulationPrimary;    // 8   CF_POPULATION_POLICY_FULL = 2: a folder is listed the first time it's opened
        public ushort PopulationModifier;   // 10
        public uint InSync;                 // 12  CF_INSYNC_POLICY_TRACK_ALL = 0x00ffffff: any change marks it "not in sync"
        public uint HardLink;               // 16
        public uint PlaceholderManagement;  // 20
    }

    public const uint ConnectFlagRequireProcessInfo = 2;   // callbacks say which program asked (CF_PROCESS_INFO)
    public const uint ConnectFlagRequireFullFilePath = 4;
    public const int ProcessInfoProcessIdOffset = 4;       // CF_PROCESS_INFO: StructSize @0, ProcessId @4

    public enum InSyncState : uint { NotInSync = 0, InSync = 1 }

    public const uint ConvertFlagMarkInSync = 1;
    public const uint UpdateFlagMarkInSync = 2;
    public const uint UpdateFlagDehydrate = 4;

    /// <summary>CF_PLACEHOLDER_BASIC_INFO: PinState @0, InSyncState @4, FileId @8, SyncRootFileId @16, IdentityLength @24, Identity @28.</summary>
    public const int BasicInfoIdentityOffset = 28;

    // ----------------------------------------------------------------------------------- calls
    [DllImport(Dll, CharSet = CharSet.Unicode)]
    public static extern int CfRegisterSyncRoot(string syncRootPath, in SyncRegistration registration, in SyncPolicies policies, uint flags);

    [DllImport(Dll, CharSet = CharSet.Unicode)]
    public static extern int CfUnregisterSyncRoot(string syncRootPath);

    [DllImport(Dll, CharSet = CharSet.Unicode)]
    public static extern int CfConnectSyncRoot(string syncRootPath, CallbackRegistration* callbackTable, nint callbackContext,
                                               uint connectFlags, out long connectionKey);

    [DllImport(Dll)]
    public static extern int CfDisconnectSyncRoot(long connectionKey);

    [DllImport(Dll)]
    public static extern int CfExecute(in OperationInfo opInfo, ref OperationParameters opParams);

    [DllImport(Dll, CharSet = CharSet.Unicode)]
    public static extern int CfCreatePlaceholders(string baseDirectoryPath, PlaceholderCreateInfo* array, uint count,
                                                  uint createFlags, out uint entriesProcessed);

    [DllImport(Dll)]
    public static extern int CfSetInSyncState(SafeFileHandle file, InSyncState state, uint flags, nint usn);

    [DllImport(Dll)]
    public static extern int CfConvertToPlaceholder(SafeFileHandle file, byte* identity, uint identityLength, uint flags,
                                                    nint usn, nint overlapped);

    [DllImport(Dll)]
    public static extern int CfUpdatePlaceholder(SafeFileHandle file, in FsMetadata metadata, byte* identity, uint identityLength,
                                                 nint dehydrateRanges, uint rangeCount, uint flags, nint usn, nint overlapped);

    /// <summary>Turns a (downloaded) placeholder back into a plain file: used for a conflict copy set aside.</summary>
    [DllImport(Dll)]
    public static extern int CfRevertPlaceholder(SafeFileHandle file, uint flags, nint overlapped);

    [DllImport(Dll)]
    public static extern int CfGetPlaceholderInfo(SafeFileHandle file, uint infoClass, byte* buffer, uint length, out uint returned);

    // -------------------------------------------------------------------------- file handles
    private const uint FileReadAttributes = 0x80;
    private const uint FileWriteAttributes = 0x100;
    private const uint ShareAll = 0x7;                  // read | write | delete: never get in a program's way
    private const uint OpenExisting = 3;
    private const uint FlagBackupSemantics = 0x02000000;   // lets a folder be opened too
    private const uint FlagOpenReparsePoint = 0x00200000;  // open the placeholder itself: never starts a download

    [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)]
    private static extern SafeFileHandle CreateFileW(string name, uint access, uint share, nint security, uint disposition,
                                                     uint flags, nint template);

    /// <summary>
    /// A handle good enough to read or set a placeholder's state (attributes only: opening it never downloads it).
    /// Throws IOException when the file can't be opened.
    /// </summary>
    public static SafeFileHandle OpenForState(string path, bool write)
    {
        var h = CreateFileW(@"\\?\" + path, FileReadAttributes | (write ? FileWriteAttributes : 0), ShareAll, 0, OpenExisting,
                            FlagBackupSemantics | FlagOpenReparsePoint, 0);
        if (h.IsInvalid)
            throw new IOException($"Couldn't open {path}", Marshal.GetHRForLastWin32Error());
        return h;
    }

    /// <summary>A handle for converting a plain file into a placeholder (needs write access to the data).</summary>
    public static SafeFileHandle OpenForConvert(string path)
    {
        const uint genericWrite = 0x40000000, genericRead = 0x80000000;
        var h = CreateFileW(@"\\?\" + path, genericRead | genericWrite, ShareAll, 0, OpenExisting, FlagBackupSemantics, 0);
        if (h.IsInvalid)
            throw new IOException($"Couldn't open {path}", Marshal.GetHRForLastWin32Error());
        return h;
    }

    /// <summary>Throws when an HRESULT says it failed (with the operation named, for the log).</summary>
    public static void Check(int hr, string what)
    {
        if (hr < 0)
            throw new IOException($"{what} failed (0x{hr:X8})", hr);
    }

    // NTSTATUS values the drive answers Windows with.
    public const int StatusSuccess = 0;
    public const int StatusUnsuccessful = unchecked((int)0xC0000001);
    public const int StatusCloudFileUnsuccessful = unchecked((int)0xC000CF0E);     // shown as "the cloud operation was unsuccessful"
    public const int StatusCloudFileAccessDenied = unchecked((int)0xC000CF18);     // the server said no
    public const int StatusCloudFileNetworkUnavailable = unchecked((int)0xC000CF07);
}
