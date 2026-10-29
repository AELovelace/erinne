# The recycle bin

Deleting in [Files](files.md) never destroys anything at once. Files and folders go to a **recycle bin**, one per location (your File box, each library), shown at the bottom of that location in the tree with the number of items in it.

## Deleting

Select one or more items and press **Delete** (toolbar, right-click, or the Delete key). A folder goes to the bin with everything inside it. You can only delete where you can change files.

While an item is in the bin it is not searchable, not visible to Claude, and not shown in the Wiki tab.

## Restoring

Open the bin, select the items and press **Restore**. They go back to where they were; if that folder was deleted too, they land at the top of the library, which needs permission to change files there (otherwise Restore is refused and the item stays in the bin). If a file with the same name has appeared there since, the restored one gets " (2)" added.

## Deleting for good

- **Delete forever** removes the selected items from the bin.
- **Empty bin** removes everything in that bin.

Both are final. Items that nobody restores are deleted for good automatically after **30 days** (admins can change the period).

## Files on the file share

For libraries that live on the organisation's file share, the bin is a folder beside the share that is hidden from Windows. Deleting a file in Windows Explorer does not use this bin: Windows' own recycle bin (if the share has one) and the NAS backups apply there instead. Deleting in the Files tab moves the file out of the share into Mnemerynne's bin, and restoring puts it back with its permissions.
