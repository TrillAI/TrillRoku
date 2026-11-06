sub Main()
    screen = CreateObject("roSGScreen")
    if screen = invalid then return

    messagePort = CreateObject("roMessagePort")
    screen.SetMessagePort(messagePort)
    scene = screen.CreateScene("MainScene")
    if scene = invalid then return

    screen.Show()
    while true
        message = wait(0, messagePort)
        if type(message) = "roSGScreenEvent" and message.IsScreenClosed() then
            return
        end if
    end while
end sub
