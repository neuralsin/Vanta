# Resolve 21.1.1 scripting API inspection

Inspected: 2026-10-09 against the locally installed Resolve MCP's version-matched `DaVinciResolveScript.pyi` stub (Resolve 21.1.1.1). The stub was written by the vendor tool to the user's temporary directory; it is not copied into this repository because the Resolve MCP bundle is marked Proprietary.

## Exact surfaces relevant to the first vertical slice

- `ProjectManager.GetCurrentProject() -> Project`
- `Project.GetUniqueId()`, `Project.GetCurrentTimeline()`, `Project.GetTimelineCount()`, `Project.GetTimelineByIndex(idx)`
- `ProjectManager.ExportProject(projectName, filePath, withStillsAndLUTs=True) -> bool`
- `Project.GetMediaPool() -> MediaPool`; `MediaPool.GetRootFolder() -> Folder`; `Folder.GetClipList() -> list[MediaPoolItem]`
- `MediaPoolItem.GetMediaId()`, `GetUniqueId()`, `GetName()`, `GetClipProperty()`
- `MediaPool.CreateEmptyTimeline(name) -> Timeline`
- `MediaPool.AppendToTimeline(clipInfos: list[AppendClipInfo]) -> list[TimelineItem]`; `AppendClipInfo` keys include `mediaPoolItem`, `startFrame`, `endFrame`, `mediaType`, `trackIndex`, and `recordFrame`.
- `MediaPool.ImportTimelineFromFile(filePath, importOptions=None) -> Timeline`; `ImportOptions` includes `timelineName`, `importSourceClips`, and source search options.
- `MediaPool.DeleteTimelines(timelines: list[Timeline]) -> bool`
- `Timeline.GetUniqueId()`, `GetName()`, `GetTrackCount(trackType)`, `GetItemListInTrack(trackType, index)`, `AddMarker(frameId, color, name, note, duration, customData=None) -> bool`, `GetMarkers() -> dict[frameId, MarkerInfo]`
- `TimelineItem.GetStart()`, `GetEnd()`, `GetSourceStartFrame()`, `GetSourceEndFrame()`, `GetDuration()`, and `GetMediaPoolItem()`
- `Project.SaveProject() -> bool`
- Render API exists (`AddRenderJob`, `StartRendering`, `GetRenderJobStatus`, `StopRendering`) but preset/settings, output path, completion fields, and file verification still need to be exercised before a VANTA proxy action can be enabled.

## Safety implications

- Every mutation must stay behind `ResolveScriptingAdapter` and its fixed broker allowlist. Never expose the vendor `run_script` methods to a model.
- A project export is a pre-mutation recovery artifact, not proof that automatic restore works.
- Automatic rollback should delete only timeline IDs that VANTA created after its snapshot. It must not infer ownership from a name or delete any baseline timeline.
- No external or pre-existing timeline should be appended to or overwritten. Timeline creation/append remains disabled by the live gate until source-frame endpoint semantics, track targeting, marker frames, save/reopen persistence, and failure rollback pass against a disposable project.
- API stub evidence is not live behavior evidence. G1/G2/G16 stay blocked until tested in Resolve Studio and verified by read-back.

## MCP inspection method

`ResolveMCP.exe --dump-tools` and its stdio JSON-RPC `get_scripting_api` / `get_scripting_docs` read-only tools were inspected. The server handshake reports Resolve Studio 21.1.1 and that Resolve is not running. No script was run inside Resolve and no project was changed.
