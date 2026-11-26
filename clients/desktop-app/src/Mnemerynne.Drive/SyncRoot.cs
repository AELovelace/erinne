using System.Runtime.InteropServices;
using System.Security.Principal;
using Windows.Security.Cryptography;
using Windows.Storage;
using Windows.Storage.Provider;

namespace Mnemerynne.Drive;

/// <summary>
/// Registering the drive's folder with Windows as a cloud "sync root", so Explorer shows it in the navigation pane
/// with the cloud status icons, and the Cloud Files callbacks reach the app. One per profile.
///
/// The WinRT way (StorageProviderSyncRootManager) gives the navigation-pane entry and the icon. It is documented
/// for packaged apps; when Windows refuses it for this unpackaged app, the plain cldapi registration is used
/// instead (the drive works the same; Explorer just doesn't add it to the navigation pane).
/// </summary>
public static class SyncRoot
{
    private const string Provider = "Mnemerynne";

    /// <summary>"Mnemerynne!S-1-5-21-…!profileId": the id Windows wants (provider!user SID!account).</summary>
    public static string IdFor(string profileId) =>
        $"{Provider}!{WindowsIdentity.GetCurrent().User!.Value}!{profileId}";

    /// <summary>Register (or re-register) the folder. Returns how it went, for the log.</summary>
    public static async Task<string> RegisterAsync(string folder, string profileId, string displayName, string iconPath,
                                                   string version, Uri? recycleBin)
    {
        Directory.CreateDirectory(folder);
        var id = IdFor(profileId);
        try
        {
            var info = new StorageProviderSyncRootInfo
            {
                Id = id,
                Path = await StorageFolder.GetFolderFromPathAsync(folder),
                DisplayNameResource = displayName,                  // the navigation pane's label
                IconResource = iconPath + ",0",                     // the app's icon
                HydrationPolicy = StorageProviderHydrationPolicy.Full,         // a file downloads whole when opened
                HydrationPolicyModifier = StorageProviderHydrationPolicyModifier.None,
                PopulationPolicy = StorageProviderPopulationPolicy.Full,       // a folder is listed when first opened
                InSyncPolicy = StorageProviderInSyncPolicy.Default,
                HardlinkPolicy = StorageProviderHardlinkPolicy.None,
                ShowSiblingsAsGroup = false,
                Version = version,
                Context = CryptographicBuffer.ConvertStringToBinary(id, BinaryStringEncoding.Utf8),
            };
            if (recycleBin is not null)
                info.RecycleBinUri = recycleBin;                     // Explorer's "View online recycle bin"
            StorageProviderSyncRootManager.Register(info);
            return "registered (navigation pane)";
        }
        catch (Exception e) when (e is COMException or UnauthorizedAccessException or ArgumentException)
        {
            // Unpackaged apps may be refused here: register the plain way.
            return RegisterPlain(folder, profileId, displayName, version) + $" (navigation pane unavailable: {e.Message.Trim()})";
        }
    }

    private static unsafe string RegisterPlain(string folder, string profileId, string displayName, string version)
    {
        var name = Marshal.StringToHGlobalUni(displayName);
        var ver = Marshal.StringToHGlobalUni(version);
        var identity = System.Text.Encoding.UTF8.GetBytes(IdFor(profileId));
        try
        {
            fixed (byte* idp = identity)
            {
                var reg = new Cf.SyncRegistration
                {
                    StructSize = (uint)sizeof(Cf.SyncRegistration),
                    ProviderName = name,
                    ProviderVersion = ver,
                    SyncRootIdentity = (nint)idp,
                    SyncRootIdentityLength = (uint)identity.Length,
                    ProviderId = new Guid("7d5b8b0e-3a4f-4c2a-9a52-6d2f0a1e5c31"),   // fixed: "the Mnemerynne drive"
                };
                var policies = new Cf.SyncPolicies
                {
                    StructSize = (uint)sizeof(Cf.SyncPolicies),
                    HydrationPrimary = 2,                            // FULL
                    PopulationPrimary = 2,                           // FULL
                    InSync = 0x00ffffff,                             // TRACK_ALL
                };
                // CF_REGISTER_FLAG_UPDATE (1): fine if it's registered already
                Cf.Check(Cf.CfRegisterSyncRoot(folder, reg, policies, 1), "Register sync root");
            }
            return "registered";
        }
        finally
        {
            Marshal.FreeHGlobal(name);
            Marshal.FreeHGlobal(ver);
        }
    }

    /// <summary>Unregister (sign-out, uninstall). The folder and any downloaded files stay where they are.</summary>
    public static void Unregister(string folder, string profileId)
    {
        try
        {
            StorageProviderSyncRootManager.Unregister(IdFor(profileId));
        }
        catch (Exception e) when (e is COMException or ArgumentException or UnauthorizedAccessException)
        {
            // not registered the WinRT way: try the plain way below
        }
        if (Directory.Exists(folder))
            Cf.CfUnregisterSyncRoot(folder);                         // harmless if it isn't registered
    }
}
