console.log("ChatGPT Bot Bridge Client V5 loaded.");

const BRIDGE = "http://127.0.0.1:8765";

function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}

let lastCommandId = null;


// =========================================================
// BRIDGE
// =========================================================

function bridgeFetch(url, method = "GET", body = undefined) {
    return new Promise((resolve, reject) => {

        chrome.runtime.sendMessage(
            {
                type: "bridgeFetch",
                url: url,
                method: method,
                body: body
            },
            response => {

                if (chrome.runtime.lastError) {
                    reject(
                        new Error(
                            chrome.runtime.lastError.message
                        )
                    );
                    return;
                }

                if (!response) {
                    reject(
                        new Error(
                            "No response from background."
                        )
                    );
                    return;
                }

                if (!response.success) {
                    reject(
                        new Error(
                            response.error ||
                            "Bridge fetch failed."
                        )
                    );
                    return;
                }

                resolve(response.data);
            }
        );
    });
}


async function getCommand() {
    const data = await bridgeFetch(
        `${BRIDGE}/command`
    );

    return data.command;
}


async function sendResult(result) {
    await bridgeFetch(
        `${BRIDGE}/set-result`,
        "POST",
        result
    );
}


// =========================================================
// CHATGPT COMPOSER
// =========================================================

function getComposer() {
    return document.querySelector(
        'div[contenteditable="true"]'
    );
}


// =========================================================
// IMAGE HELPERS
// =========================================================

function base64ToFile(
    base64Data,
    fileName,
    mimeType
) {

    const byteCharacters =
        atob(base64Data);

    const byteNumbers =
        new Array(
            byteCharacters.length
        );

    for (
        let i = 0;
        i < byteCharacters.length;
        i++
    ) {
        byteNumbers[i] =
            byteCharacters.charCodeAt(i);
    }

    const byteArray =
        new Uint8Array(
            byteNumbers
        );

    const blob =
        new Blob(
            [byteArray],
            {
                type:
                    mimeType ||
                    "image/jpeg"
            }
        );

    return new File(
        [blob],
        fileName || "image.jpg",
        {
            type:
                mimeType ||
                "image/jpeg"
        }
    );
}


async function attachImageByPaste(file) {

    const composer =
        getComposer();

    if (!composer) {
        throw new Error(
            "ChatGPT composer not found."
        );
    }

    composer.focus();

    const dataTransfer =
        new DataTransfer();

    dataTransfer.items.add(
        file
    );

    const pasteEvent =
        new ClipboardEvent(
            "paste",
            {
                clipboardData:
                    dataTransfer,

                bubbles: true,
                cancelable: true
            }
        );

    composer.dispatchEvent(
        pasteEvent
    );

    await sleep(3000);
}


// =========================================================
// REPLY HELPERS
// =========================================================

function cleanReply(rawReply) {

    if (
        Array.isArray(rawReply)
    ) {
        return rawReply[0] || "";
    }

    if (
        typeof rawReply === "string"
    ) {
        return rawReply;
    }

    return String(
        rawReply || ""
    );
}


async function getReliableReply(
    rawReply
) {

    let replyText =
        cleanReply(
            rawReply
        );

    console.log(
        "Initial reply:",
        rawReply
    );


    // -----------------------------------------------------
    // اگر askAndGetReply جواب داده
    // -----------------------------------------------------

    if (
        replyText.trim()
    ) {

        console.log(
            "Using askAndGetReply result."
        );

        return replyText;
    }


    // -----------------------------------------------------
    // fallback 1
    // -----------------------------------------------------

    console.log(
        "Initial reply empty. Waiting for final ChatGPT response..."
    );

    await sleep(3000);

    try {

        const lastReply =
            await chatgpt.getLastResponse();

        console.log(
            "getLastResponse result:",
            lastReply
        );

        replyText =
            cleanReply(
                lastReply
            );

    }
    catch (error) {

        console.warn(
            "getLastResponse failed:",
            error
        );
    }


    // -----------------------------------------------------
    // fallback 2
    // -----------------------------------------------------

    if (
        !replyText.trim()
    ) {

        console.log(
            "Reply still empty. Retrying..."
        );

        await sleep(3000);

        try {

            const lastReply =
                await chatgpt.getLastResponse();

            console.log(
                "getLastResponse retry:",
                lastReply
            );

            replyText =
                cleanReply(
                    lastReply
                );

        }
        catch (error) {

            console.warn(
                "getLastResponse retry failed:",
                error
            );
        }
    }


    // -----------------------------------------------------
    // هنوز خالی
    // -----------------------------------------------------

    if (
        !replyText.trim()
    ) {

        throw new Error(
            "ChatGPT returned an empty response."
        );
    }


    return replyText;
}


// =========================================================
// COMMAND PROCESSOR
// =========================================================

async function processCommand(
    command
) {

    if (!command) {
        return;
    }


    // -----------------------------------------------------
    // جلوگیری از اجرای دوباره command
    // -----------------------------------------------------

    if (
        command.id &&
        command.id === lastCommandId
    ) {
        return;
    }

    if (
        command.id
    ) {
        lastCommandId =
            command.id;
    }


    // =====================================================
    // PING
    // =====================================================

    if (
        command.type === "ping"
    ) {

        await sendResult({
            id:
                command.id || null,

            type:
                "pong",

            value:
                "PONG"
        });

        return;
    }


    // =====================================================
    // ASK
    // =====================================================

    if (
        command.type === "ask"
    ) {

        try {

            console.log(
                "ASK command received:",
                command.id
            );

            const rawReply =
                await chatgpt.askAndGetReply(
                    command.text
                );

            const replyText =
                await getReliableReply(
                    rawReply
                );

            console.log(
                "ASK final reply:",
                replyText
            );

            await sendResult({

                id:
                    command.id || null,

                type:
                    "answer",

                success:
                    true,

                text:
                    replyText
            });

        }
        catch (error) {

            console.error(
                "ASK ERROR:",
                error
            );

            await sendResult({

                id:
                    command.id || null,

                type:
                    "answer",

                success:
                    false,

                error:
                    String(error)
            });
        }

        return;
    }


    // =====================================================
    // VISION
    // =====================================================

    if (
        command.type === "vision"
    ) {

        try {

            console.log(
                "VISION command received:",
                command.id
            );


            // ---------------------------------------------
            // ساخت فایل
            // ---------------------------------------------

            const file =
                base64ToFile(
                    command.imageBase64,
                    command.fileName,
                    command.mimeType
                );


            console.log(
                "VISION file created:",
                file.name,
                file.type,
                file.size
            );


            // ---------------------------------------------
            // attach تصویر
            // ---------------------------------------------

            await attachImageByPaste(
                file
            );


            console.log(
                "Image attached."
            );


            // ---------------------------------------------
            // ارسال Prompt
            // ---------------------------------------------

            const rawReply =
                await chatgpt.askAndGetReply(
                    command.prompt
                );


            // ---------------------------------------------
            // جواب مطمئن
            // ---------------------------------------------

            const replyText =
                await getReliableReply(
                    rawReply
                );


            console.log(
                "FINAL VISION REPLY:",
                replyText
            );


            console.log(
                "VISION reply:",
                replyText
            );


            // ---------------------------------------------
            // ارسال نتیجه به Python
            // ---------------------------------------------

            await sendResult({

                id:
                    command.id || null,

                type:
                    "vision-answer",

                success:
                    true,

                text:
                    replyText
            });

        }
        catch (error) {

            console.error(
                "VISION ERROR:",
                error
            );


            await sendResult({

                id:
                    command.id || null,

                type:
                    "vision-answer",

                success:
                    false,

                error:
                    String(error)
            });
        }

        return;
    }
}


// =========================================================
// BRIDGE LOOP
// =========================================================

async function bridgeLoop() {

    console.log(
        "Bridge polling started."
    );


    while (true) {

        try {

            const command =
                await getCommand();


            if (command) {

                await processCommand(
                    command
                );
            }

        }
        catch (error) {


            // ---------------------------------------------
            // context قدیمی بعد از Reload
            // ---------------------------------------------

            if (
                String(error)
                    .includes(
                        "Extension context invalidated"
                    )
            ) {

                console.warn(
                    "Old extension context stopped."
                );

                return;
            }


            // ---------------------------------------------
            // Bridge موقتاً خاموش
            // ---------------------------------------------

            if (
                String(error)
                    .includes(
                        "Failed to fetch"
                    )
            ) {

                console.warn(
                    "Bridge temporarily unavailable."
                );

            } else {

                console.error(
                    "Bridge polling error:",
                    error
                );
            }
        }


        await sleep(1000);
    }
}


// =========================================================
// START
// =========================================================

bridgeLoop();