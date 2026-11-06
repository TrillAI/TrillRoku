sub init()
    m.top.functionName = "fetchManifest"
end sub

sub fetchManifest()
    baseUrl = Trim(m.top.apiBaseUrl)
    sourceUrl = Trim(m.top.sourceUrl)
    if Len(baseUrl) = 0 or Len(sourceUrl) = 0 then
        m.top.errorMessage = "Configure the API base URL and a source URL first."
        return
    end if

    while Len(baseUrl) > 0 and Right(baseUrl, 1) = "/"
        baseUrl = Left(baseUrl, Len(baseUrl) - 1)
    end while
    endpoint = baseUrl + "/api/manifest"

    transfer = CreateObject("roUrlTransfer")
    if transfer = invalid then
        m.top.errorMessage = "Could not create the Roku HTTP client."
        return
    end if

    transfer.SetCertificatesFile("common:/certs/ca-bundle.crt")
    transfer.InitClientCertificates()
    transfer.SetUrl(endpoint)
    transfer.AddHeader("Content-Type", "application/json")
    transfer.AddHeader("Accept", "application/json")
    apiKey = Trim(m.top.apiKey)
    if Len(apiKey) > 0 then transfer.AddHeader("X-API-Key", apiKey)

    sourceValue = LCase(sourceUrl)
    requestPayload = {}
    if Left(sourceValue, 7) = "http://" or Left(sourceValue, 8) = "https://" then
        requestPayload.url = sourceUrl
    else
        ' Android creates a short-lived scan code; enter it on the Roku instead
        ' of retyping a long website URL to load the exact same video list.
        requestPayload.scanId = sourceUrl
    end if
    requestBody = FormatJson(requestPayload)
    responseBody = transfer.PostFromString(requestBody)
    responseCode = transfer.GetResponseCode()
    if responseCode < 200 or responseCode >= 300 then
        errorText = "Manifest API request failed (HTTP " + responseCode.ToStr() + ")."
        if responseBody <> invalid then
            if Len(responseBody) > 0 then
                errorPayload = ParseJson(responseBody)
                if errorPayload <> invalid then
                    if type(errorPayload) = "roAssociativeArray" and errorPayload.error <> invalid then
                        errorText = errorPayload.error
                    end if
                end if
            end if
        end if
        m.top.errorMessage = errorText
        return
    end if
    if responseBody = invalid or Len(responseBody) = 0 then
        m.top.errorMessage = "The manifest API returned an empty response."
        return
    end if

    payload = ParseJson(responseBody)
    if payload = invalid or type(payload) <> "roAssociativeArray" then
        m.top.errorMessage = "The manifest API returned invalid JSON."
        return
    end if
    records = payload.items
    if records = invalid or type(records) <> "roArray" then
        m.top.errorMessage = "The manifest response did not contain an items array."
        return
    end if

    manifestRoot = CreateObject("roSGNode", "ContentNode")
    videoRow = manifestRoot.CreateChild("ContentNode")
    videoRow.title = "Videos"

    for each record in records
        if type(record) = "roAssociativeArray" then
            streamUrl = record.url
            if streamUrl <> invalid then
                if type(streamUrl) = "String" then
                    if Len(Trim(streamUrl)) > 0 then
                        streamFormat = LCase(Trim(SafeString(record.streamFormat, "")))
                        if streamFormat = "mp4" or streamFormat = "hls" then
                            item = videoRow.CreateChild("ContentNode")
                            item.title = SafeString(record.title, "Untitled video")
                            item.HDPosterUrl = SafeString(record.thumb, "")
                            item.url = streamUrl
                            item.streamFormat = streamFormat
                        end if
                    end if
                end if
            end if
        end if
    end for

    m.top.errorMessage = ""
    m.top.manifest = manifestRoot
end sub

function SafeString(value as Dynamic, fallback as String) as String
    if value = invalid then return fallback
    if type(value) <> "String" then return fallback
    return value
end function
