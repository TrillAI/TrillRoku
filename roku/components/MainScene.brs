sub init()
    m.apiBaseUrl = Trim(m.top.apiBaseUrl)
    m.apiKey = Trim(m.top.apiKey)
    m.sourceUrl = Trim(m.top.initialSourceUrl)
    m.currentIndex = -1
    m.entries = []
    m.isLoading = false

    m.posterGrid = m.top.findNode("posterGrid")
    m.video = m.top.findNode("video")
    m.status = m.top.findNode("status")
    m.buttonGroup = m.top.findNode("buttonGroup")
    m.manifestTask = m.top.findNode("manifestTask")

    m.posterGrid.observeField("itemSelected", "onPosterSelected")
    m.video.observeField("content", "onContentChange")
    m.video.observeField("state", "onVideoStateChanged")
    m.manifestTask.observeField("manifest", "onManifestLoaded")
    m.manifestTask.observeField("errorMessage", "onManifestError")
    m.buttonGroup.observeField("buttonSelected", "onButtonSelected")
    m.buttonGroup.buttons = ["Change Source / Scan Code"]

    m.video.visible = false
    m.top.setFocus(true)
    if Len(m.sourceUrl) > 0 then
        loadManifest()
    else
        setStatus("Enter a website URL, direct stream URL, or Android companion scan code.")
        showSourceDialog()
    end if
end sub

sub onButtonSelected(_event as Object)
    showSourceDialog()
end sub

sub showSourceDialog()
    dialog = CreateObject("roSGNode", "KeyboardDialog")
    if dialog = invalid then
        setStatus("Could not open the URL keyboard.")
        return
    end if
    dialog.title = "Website URL or scan code"
    dialog.message = "Enter a website URL, direct MP4/HLS stream URL, or scan code from TrillRoku Companion."
    dialog.text = m.sourceUrl
    dialog.buttons = ["Load", "Cancel"]
    dialog.observeField("buttonSelected", "onSourceDialogButtonSelected")
    m.sourceDialog = dialog
    m.top.dialog = dialog
end sub

sub onSourceDialogButtonSelected(event as Object)
    selectedButton = event.getData()
    if selectedButton = 0 then
        candidateUrl = Trim(m.sourceDialog.text)
        if Len(candidateUrl) = 0 then
            setStatus("A source URL is required.")
        else
            m.sourceUrl = candidateUrl
            m.video.control = "stop"
            m.video.content = invalid
            m.video.visible = false
            m.entries = []
            m.currentIndex = -1
            m.posterGrid.content = invalid
            m.posterGrid.visible = true
            m.buttonGroup.visible = true
            loadManifest()
        end if
    end if
    m.top.dialog = invalid
    m.sourceDialog = invalid
end sub

sub loadManifest()
    if Len(m.apiBaseUrl) = 0 or Instr(1, LCase(m.apiBaseUrl), "your_api_host") > 0 or Instr(1, LCase(m.apiBaseUrl), "replace-me") > 0 then
        setStatus("Configure apiBaseUrl in components/MainScene.xml, then rebuild the Roku package.")
        return
    end if
    if Len(m.sourceUrl) = 0 then
        showSourceDialog()
        return
    end if
    if m.isLoading then
        setStatus("A manifest request is already running.")
        return
    end if

    m.isLoading = true
    setStatus("Loading the manifest from the API…")
    m.manifestTask.apiBaseUrl = m.apiBaseUrl
    m.manifestTask.apiKey = m.apiKey
    m.manifestTask.sourceUrl = m.sourceUrl
    m.manifestTask.control = "RUN"
end sub

sub onManifestLoaded(event as Object)
    root = event.getData()
    m.isLoading = false
    if root = invalid then
        setStatus("The API returned no manifest. Check the API logs and source page.")
        return
    end if
    if root.getChildCount() < 1 then
        setStatus("The manifest contains no video items.")
        return
    end if

    row = root.getChild(0)
    m.entries = []
    itemCount = row.getChildCount()
    if itemCount > 0 then
        for index = 0 to itemCount - 1
            m.entries.Push(row.getChild(index))
        end for
    end if
    if m.entries.Count() = 0 then
        m.posterGrid.content = root
        setStatus("No direct MP4/HLS streams were found on this page.")
        return
    end if

    m.currentIndex = -1
    m.posterGrid.content = root
    m.posterGrid.visible = true
    m.buttonGroup.visible = true
    setStatus("Found " + m.entries.Count().ToStr() + " videos. Select a poster to play; the playlist loops automatically.")
    m.posterGrid.setFocus(true)
end sub

sub onManifestError(event as Object)
    message = event.getData()
    if message = invalid then return
    if Len(message) = 0 then return
    m.isLoading = false
    setStatus(message)
end sub

sub onPosterSelected(event as Object)
    index = event.getData()
    if index = invalid then return
    if index < 0 or index >= m.entries.Count() then return
    playEntryAt(index)
end sub

sub playEntryAt(index as Integer)
    if m.entries.Count() = 0 then return
    if index < 0 or index >= m.entries.Count() then return

    m.currentIndex = index
    m.video.visible = true
    m.posterGrid.visible = false
    m.buttonGroup.visible = false
    m.status.visible = false
    m.top.setFocus(true)

    ' Create a fresh node for each play, including the one-item wrap case, so
    ' Video.content always changes and onContentChange reliably fires.
    entry = m.entries[index]
    playableContent = CreateObject("roSGNode", "ContentNode")
    playableContent.title = entry.title
    playableContent.HDPosterUrl = entry.HDPosterUrl
    playableContent.url = entry.url
    playableContent.streamFormat = entry.streamFormat
    m.video.content = playableContent
end sub

sub onContentChange(event as Object)
    content = event.getData()
    if content = invalid then return

    ' Transition: selecting a ContentNode changes Video.content. This observer
    ' is the one place that starts newly selected / automatically advanced items.
    m.video.visible = true
    m.video.control = "play"
end sub

sub onVideoStateChanged(event as Object)
    state = event.getData()
    if state = invalid then return
    normalizedState = LCase(state)

    if normalizedState = "finished" then
        ' Video.state == "finished" is SceneGraph's completion signal. Wrap at
        ' the end so playback continues as a continuous playlist.
        if m.entries.Count() > 0 and m.currentIndex >= 0 then
            nextIndex = m.currentIndex + 1
            if nextIndex >= m.entries.Count() then nextIndex = 0
            playEntryAt(nextIndex)
        end if
    else if normalizedState = "error" then
        m.video.control = "stop"
        m.video.visible = false
        m.posterGrid.visible = true
        m.buttonGroup.visible = true
        m.status.visible = true
        setStatus("This stream could not be played. Select another poster or change the source URL.")
        m.posterGrid.setFocus(true)
    end if
end sub

function onKeyEvent(key as String, press as Boolean) as Boolean
    if not press then return false

    if key = "back" and m.video.visible then
        m.video.control = "stop"
        m.video.visible = false
        m.posterGrid.visible = true
        m.buttonGroup.visible = true
        m.status.visible = true
        m.posterGrid.setFocus(true)
        return true
    end if

    if key = "options" then
        showSourceDialog()
        return true
    end if
    return false
end function

sub setStatus(message as String)
    if m.status <> invalid then
        m.status.text = message
        m.status.visible = true
    end if
end sub
