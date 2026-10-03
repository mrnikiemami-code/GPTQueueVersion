console.log("ChatGPT Queue Bridge Client V8 loaded.");

const BRIDGE = "http://127.0.0.1:8767";

const VISION_FIELDS = [
    "instagramId",
    "fullName",
    "phoneNumber",
    "job",
    "employeeCount",
    "city",
    "challenge",
    "description"
];


function sleep(ms) {
    return new Promise(resolve => setTimeout(resolve, ms));
}


let lastCommandId = null;


// =========================================================
// BRIDGE
// =========================================================

function bridgeFetch(
    url,
    method = "GET",
    body = undefined
) {

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

    const selectors = [
        '#prompt-textarea',
        'div#prompt-textarea[contenteditable="true"]',
        'div[contenteditable="true"][data-lexical-editor="true"]',
        'div.ProseMirror[contenteditable="true"]',
        'div[contenteditable="true"]'
    ];


    for (const selector of selectors) {

        const element =
            document.querySelector(
                selector
            );


        if (element) {

            return element;
        }
    }


    return null;
}


async function waitForComposer(
    timeoutMs = 10000
) {

    const startedAt =
        Date.now();


    while (
        Date.now() - startedAt <
        timeoutMs
    ) {

        const composer =
            getComposer();


        if (composer) {

            return composer;
        }


        await sleep(200);
    }


    throw new Error(
        "ChatGPT composer not found."
    );
}


function getSendButton() {

    const selectors = [
        'button[data-testid="send-button"]',
        'button[aria-label="Send prompt"]',
        'button[aria-label="Send message"]'
    ];


    for (const selector of selectors) {

        const button =
            document.querySelector(
                selector
            );


        if (button) {

            return button;
        }
    }


    return null;
}


async function sendPromptDirectly(
    prompt
) {

    const composer =
        await waitForComposer();


    composer.focus();


    const promptText =
        String(
            prompt || ""
        ).trim();


    if (!promptText) {

        throw new Error(
            "Vision prompt is empty."
        );
    }


    // =====================================================
    // IMPORTANT
    //
    // Do NOT write textContent/innerHTML directly.
    // ChatGPT currently uses ProseMirror + React state.
    //
    // A real-looking paste event lets ProseMirror update
    // its own document/state, so ChatGPT changes the
    // Start Voice button into the Send button.
    // =====================================================

    const dataTransfer =
        new DataTransfer();


    dataTransfer.setData(
        "text/plain",
        promptText
    );


    const pasteEvent =
        new ClipboardEvent(
            "paste",
            {
                clipboardData:
                    dataTransfer,

                bubbles:
                    true,

                cancelable:
                    true,

                composed:
                    true
            }
        );


    const pasteAccepted =
        composer.dispatchEvent(
            pasteEvent
        );


    console.log(
        "VISION prompt paste dispatched.",
        {
            pasteAccepted,
            textLength:
                promptText.length
        }
    );


    // Give ProseMirror/React time to commit its state.
    await sleep(700);


    console.log(
        "VISION composer after paste:",
        {
            text:
                (
                    composer.innerText ||
                    composer.textContent ||
                    ""
                ).slice(
                    0,
                    300
                ),

            html:
                composer.innerHTML.slice(
                    0,
                    500
                )
        }
    );


    // =====================================================
    // WAIT FOR CHATGPT TO ENABLE / RENDER SEND BUTTON
    // =====================================================

    const startedAt =
        Date.now();


    while (
        Date.now() - startedAt <
        12000
    ) {

        const form =
            composer.closest(
                'form[data-chatgpt-composer]'
            )
            ||
            composer.closest(
                "form"
            );


        const root =
            form ||
            document;


        const selectors = [

            'button[data-testid="send-button"]',

            'button[aria-label="Send prompt"]',

            'button[aria-label="Send message"]',

            'button[aria-label="Send"]'
        ];


        let sendButton =
            null;


        for (
            const selector
            of selectors
        ) {

            const candidate =
                root.querySelector(
                    selector
                );


            if (candidate) {

                sendButton =
                    candidate;

                break;
            }
        }


        // Fallback:
        // Current ChatGPT can change aria-label dynamically.
        if (!sendButton) {

            const buttons =
                Array.from(
                    root.querySelectorAll(
                        "button"
                    )
                );


            sendButton =
                buttons.find(
                    button => {

                        const label =
                            String(
                                button.getAttribute(
                                    "aria-label"
                                ) || ""
                            ).trim();


                        return (
                            /^send\b/i.test(
                                label
                            )
                            &&
                            !button.disabled
                            &&
                            button.getAttribute(
                                "aria-disabled"
                            ) !== "true"
                        );
                    }
                )
                ||
                null;
        }


        if (
            sendButton
            &&
            !sendButton.disabled
            &&
            sendButton.getAttribute(
                "aria-disabled"
            ) !== "true"
        ) {

            console.log(
                "VISION send button found:",
                {
                    ariaLabel:
                        sendButton.getAttribute(
                            "aria-label"
                        ),

                    testId:
                        sendButton.getAttribute(
                            "data-testid"
                        )
                }
            );


            sendButton.click();


            console.log(
                "VISION prompt sent."
            );


            await sleep(
                500
            );


            return;
        }


        await sleep(
            200
        );
    }


    // =====================================================
    // DEBUG INFORMATION
    // =====================================================

    const form =
        composer.closest(
            'form[data-chatgpt-composer]'
        )
        ||
        composer.closest(
            "form"
        );


    const root =
        form ||
        document;


    const buttonLabels =
        Array.from(
            root.querySelectorAll(
                "button"
            )
        )
        .map(
            button => ({
                label:
                    button.getAttribute(
                        "aria-label"
                    ),

                disabled:
                    button.disabled,

                ariaDisabled:
                    button.getAttribute(
                        "aria-disabled"
                    )
            })
        );


    console.error(
        "VISION composer/send debug:",
        {
            composerText:
                (
                    composer.innerText ||
                    composer.textContent ||
                    ""
                ).slice(
                    0,
                    1000
                ),

            buttonLabels
        }
    );


    throw new Error(
        "ChatGPT did not expose an enabled Send button after ProseMirror paste."
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


    // فرصت برای آپلود کامل تصویر
    await sleep(3000);
}


// =========================================================
// REPLY HELPERS
// =========================================================

function cleanReply(rawReply) {

    if (Array.isArray(rawReply)) {

        return rawReply[0] || "";
    }


    if (typeof rawReply === "string") {

        return rawReply;
    }


    return String(
        rawReply || ""
    );
}


// =========================================================
// JSON HELPERS
// =========================================================

function extractJsonCandidate(text) {

    if (!text) {
        return "";
    }


    let value =
        String(text).trim();


    // حذف code fence
    value = value
        .replace(/^```json\s*/i, "")
        .replace(/^```\s*/i, "")
        .replace(/\s*```$/i, "")
        .trim();


    // اگر متن اضافه قبل/بعد JSON وجود داشت
    const firstBrace =
        value.indexOf("{");

    const lastBrace =
        value.lastIndexOf("}");


    if (
        firstBrace !== -1 &&
        lastBrace !== -1 &&
        lastBrace > firstBrace
    ) {

        value = value.substring(
            firstBrace,
            lastBrace + 1
        );
    }


    return value.trim();
}


// =========================================================
// ROBUST VISION JSON EXTRACTION
// =========================================================

function extractVisionJsonFromAnything(value) {

    if (value === null || value === undefined) {
        return null;
    }


    // -----------------------------------------------------
    // Array: همه اعضا را از آخر به اول بررسی کن
    // -----------------------------------------------------

    if (Array.isArray(value)) {

        for (
            let i = value.length - 1;
            i >= 0;
            i--
        ) {

            const found =
                extractVisionJsonFromAnything(
                    value[i]
                );

            if (found) {
                return found;
            }
        }

        return null;
    }


    // -----------------------------------------------------
    // Object: اول خودش، بعد اعضای تو در تو
    // -----------------------------------------------------

    if (
        typeof value === "object"
    ) {

        const normalized =
            normalizeVisionObject(
                value
            );

        if (normalized) {
            return normalized;
        }


        for (
            const key
            of Object.keys(value)
        ) {

            const found =
                extractVisionJsonFromAnything(
                    value[key]
                );

            if (found) {
                return found;
            }
        }

        return null;
    }


    // -----------------------------------------------------
    // فقط string
    // -----------------------------------------------------

    if (
        typeof value !== "string"
    ) {
        return null;
    }


    let text =
        value
            .trim()
            .replace(
                /^```(?:json)?\s*/i,
                ""
            )
            .replace(
                /\s*```$/i,
                ""
            )
            .trim();


    if (!text) {
        return null;
    }


    // -----------------------------------------------------
    // تلاش اول: کل متن JSON باشد
    // -----------------------------------------------------

    try {

        const parsed =
            JSON.parse(
                text
            );


        const normalized =
            normalizeVisionObject(
                parsed
            );


        if (normalized) {
            return normalized;
        }


        const nested =
            extractVisionJsonFromAnything(
                parsed
            );


        if (nested) {
            return nested;
        }

    } catch (_) {}


    // -----------------------------------------------------
    // تلاش دوم:
    // JSON objectهای متوازن داخل متن را استخراج کن
    // -----------------------------------------------------

    const candidates = [];


    let start = -1;
    let depth = 0;
    let inString = false;
    let escaped = false;


    for (
        let i = 0;
        i < text.length;
        i++
    ) {

        const ch =
            text[i];


        if (inString) {

            if (escaped) {

                escaped = false;

            } else if (
                ch === "\\"
            ) {

                escaped = true;

            } else if (
                ch === '"'
            ) {

                inString = false;
            }

            continue;
        }


        if (
            ch === '"'
        ) {

            inString = true;
            continue;
        }


        if (
            ch === "{"
        ) {

            if (
                depth === 0
            ) {

                start = i;
            }

            depth++;

        } else if (
            ch === "}"
        ) {

            if (
                depth > 0
            ) {

                depth--;


                if (
                    depth === 0 &&
                    start !== -1
                ) {

                    candidates.push(
                        text.slice(
                            start,
                            i + 1
                        )
                    );

                    start = -1;
                }
            }
        }
    }


    // معمولاً JSON نهایی در انتهای پاسخ است
    for (
        let i =
            candidates.length - 1;
        i >= 0;
        i--
    ) {

        try {

            const parsed =
                JSON.parse(
                    candidates[i]
                );


            const normalized =
                normalizeVisionObject(
                    parsed
                );


            if (normalized) {
                return normalized;
            }


            const nested =
                extractVisionJsonFromAnything(
                    parsed
                );


            if (nested) {
                return nested;
            }

        } catch (_) {}
    }


    return null;
}


// =========================================================
// NORMALIZE VISION OBJECT
// =========================================================

function normalizeVisionObject(obj) {

    if (
        !obj ||
        typeof obj !== "object" ||
        Array.isArray(obj)
    ) {

        return null;
    }


    // حداقل باید یکی از فیلدهای Vision را داشته باشد
    const hasVisionField =
        VISION_FIELDS.some(
            field =>
                Object.prototype
                    .hasOwnProperty
                    .call(
                        obj,
                        field
                    )
        );


    if (!hasVisionField) {
        return null;
    }


    const normalized = {};


    for (
        const field
        of VISION_FIELDS
    ) {

        const value =
            obj[field];


        if (
            value === null ||
            value === undefined
        ) {

            normalized[field] = "";

        } else if (
            typeof value === "string"
        ) {

            normalized[field] =
                value.trim();

        } else {

            normalized[field] =
                String(value).trim();
        }
    }


    return JSON.stringify(
        normalized
    );
}


// =========================================================
// PARSE VISION JSON
// =========================================================

function parseValidVisionJson(value) {

    try {

        return (
            extractVisionJsonFromAnything(
                value
            ) || null
        );

    } catch (error) {

        console.warn(
            "parseValidVisionJson error:",
            error
        );

        return null;
    }
}


function parseValidVisionJsonFromReply(rawReply) {

    return parseValidVisionJson(
        rawReply
    );
}


// =========================================================
// NORMAL TEXT REPLY
// =========================================================

async function getReliableReply(rawReply) {

    let replyText =
        cleanReply(
            rawReply
        );


    console.log(
        "Initial reply:",
        rawReply
    );


    if (
        replyText.trim()
    ) {

        console.log(
            "Using askAndGetReply result."
        );

        return replyText;
    }


    console.log(
        "Initial reply empty. Waiting..."
    );


    for (
        let attempt = 1;
        attempt <= 10;
        attempt++
    ) {

        await sleep(3000);


        try {

            const lastReply =
                await chatgpt.getLastResponse();


            console.log(
                `getLastResponse attempt ${attempt}:`,
                lastReply
            );


            replyText =
                cleanReply(
                    lastReply
                );


            if (
                replyText.trim()
            ) {

                return replyText;
            }

        } catch (error) {

            console.warn(
                "getLastResponse failed:",
                error
            );
        }
    }


    throw new Error(
        "ChatGPT returned an empty response."
    );
}


// =========================================================
// ASSISTANT DOM HELPERS
// =========================================================

function getAssistantNodeText(node) {

    if (!node) {
        return "";
    }


    return String(
        node.textContent
        ||
        node.innerText
        ||
        ""
    )
        .replace(
            /[\u200B-\u200D\uFEFF]/g,
            ""
        )
        .replace(
            /\u00A0/g,
            " "
        )
        .replace(
            /\r/g,
            ""
        )
        .trim();
}


function getAssistantMessageNodes() {

    // =====================================================
    // CURRENT CHATGPT DOM (2026)
    //
    // Preferred structure observed in the live UI:
    //
    // <div
    //   data-content-search-unit-key="...:assistant"
    //   data-chatgpt-search-unit-key="...:assistant"
    // >
    //   ...
    //   <div data-markdown-text-style="assistant-message">
    //      <p>
    //        <span>...</span>
    //        <bdi>...</bdi>
    //      </p>
    //   </div>
    // </div>
    //
    // IMPORTANT:
    // We read the parent node with textContent so split
    // fragments such as SPAN + BDI are reassembled.
    // =====================================================


    const primarySelectors = [

        '[data-content-search-unit-key$=":assistant"]',

        '[data-chatgpt-search-unit-key$=":assistant"]',

        '[data-content-search-unit-key*="assistant"]',

        '[data-chatgpt-search-unit-key*="assistant"]'
    ];


    for (
        const selector
        of primarySelectors
    ) {

        const nodes =
            Array.from(
                document.querySelectorAll(
                    selector
                )
            )
            .filter(
                node =>
                    node
                    &&
                    !node.closest(
                        'form[data-chatgpt-composer]'
                    )
                    &&
                    getAssistantNodeText(
                        node
                    )
            );


        if (
            nodes.length > 0
        ) {

            return nodes;
        }
    }


    // =====================================================
    // CURRENT ASSISTANT MARKDOWN FALLBACK
    // =====================================================

    const markdownNodes =
        Array.from(
            document.querySelectorAll(
                '[data-markdown-text-style="assistant-message"]'
            )
        )
        .filter(
            node =>
                node
                &&
                !node.closest(
                    'form[data-chatgpt-composer]'
                )
                &&
                getAssistantNodeText(
                    node
                )
        );


    if (
        markdownNodes.length > 0
    ) {

        return markdownNodes;
    }


    // =====================================================
    // LEGACY FALLBACKS
    // =====================================================

    const legacySelectors = [

        '[data-message-author-role="assistant"]',

        'article[data-testid^="conversation-turn-"] .markdown',

        '[data-testid^="conversation-turn-"] .markdown',

        'article .markdown.prose',

        '.markdown.prose',

        '.markdown'
    ];


    const found = [];

    const seen =
        new Set();


    for (
        const selector
        of legacySelectors
    ) {

        const nodes =
            document.querySelectorAll(
                selector
            );


        for (
            const node
            of nodes
        ) {

            if (
                !node
                ||
                seen.has(
                    node
                )
            ) {

                continue;
            }


            if (
                node.closest(
                    'form[data-chatgpt-composer]'
                )
            ) {

                continue;
            }


            const nodeText =
                getAssistantNodeText(
                    node
                );


            if (
                !nodeText
            ) {

                continue;
            }


            seen.add(
                node
            );

            found.push(
                node
            );
        }
    }


    return found;
}


function getAssistantTurnRecords() {

    const records = [];

    const seenIds =
        new Set();


    // Current ChatGPT UI exposes a stable per-message id on the outer
    // assistant turn. Prefer this over DOM position or node object identity.
    const outerTurns =
        Array.from(
            document.querySelectorAll(
                '[data-chatgpt-search-message-ids]'
            )
        );


    for (
        const node
        of outerTurns
    ) {

        if (
            !node
            ||
            node.closest(
                'form[data-chatgpt-composer]'
            )
        ) {

            continue;
        }


        const roleNode =
            node.querySelector(
                '[data-conversation-role="assistant"]'
            );


        const markdownNode =
            node.querySelector(
                '[data-markdown-text-style="assistant-message"]'
            );


        if (
            !roleNode
            &&
            !markdownNode
        ) {

            continue;
        }


        const messageId =
            String(
                node.getAttribute(
                    "data-chatgpt-search-message-ids"
                )
                ||
                ""
            ).trim();


        const text =
            getAssistantNodeText(
                markdownNode || node
            );


        if (
            !messageId
            ||
            !text
        ) {

            continue;
        }


        if (
            seenIds.has(
                messageId
            )
        ) {

            continue;
        }


        seenIds.add(
            messageId
        );


        records.push({

            node,

            messageId,

            text
        });
    }


    return records;
}


function getAssistantSnapshot() {

    const nodes =
        getAssistantMessageNodes();


    const entries =
        nodes.map(
            node => ({

                node,

                text:
                    getAssistantNodeText(
                        node
                    )
            })
        );


    const assistantMessageIds = [];


    const selectionNodes =
        Array.from(
            document.querySelectorAll(
                '[data-chatgpt-selection-message-id]'
            )
        );


    for (
        const selectionNode
        of selectionNodes
    ) {

        const turn =
            selectionNode.closest(
                '[data-chatgpt-search-message-ids]'
            );


        if (!turn) {
            continue;
        }


        const isAssistant =
            Boolean(
                turn.querySelector(
                    '[data-conversation-role="assistant"]'
                )
                ||
                turn.querySelector(
                    '[data-markdown-text-style="assistant-message"]'
                )
            );


        if (!isAssistant) {
            continue;
        }


        const messageId =
            String(
                selectionNode.getAttribute(
                    'data-chatgpt-selection-message-id'
                )
                || ""
            ).trim();


        if (
            messageId
            &&
            !assistantMessageIds.includes(
                messageId
            )
        ) {

            assistantMessageIds.push(
                messageId
            );
        }
    }


    const texts =
        entries
            .map(
                entry =>
                    entry.text
            )
            .filter(
                Boolean
            );


    const lastText =
        texts.length > 0
            ? texts[texts.length - 1]
            : "";


    return {

        count:
            nodes.length,

        lastText,

        nodes,

        texts,

        entries,

        assistantMessageIds
    };
}

function getLatestAssistantText() {

    const nodes =
        getAssistantMessageNodes();


    for (
        let i =
            nodes.length - 1;
        i >= 0;
        i--
    ) {

        const text =
            getAssistantNodeText(
                nodes[i]
            );


        if (
            text
        ) {

            return text;
        }
    }


    return "";
}


// =========================================================
// VISION FINAL JSON REPLY
// =========================================================

async function getReliableVisionReply(
    rawReply,
    assistantBaseline
) {

    console.log(
        "Initial VISION reply:",
        rawReply
    );


    let validJson =
        parseValidVisionJsonFromReply(
            rawReply
        );


    if (validJson) {

        console.log(
            "VALID FINAL VISION JSON FOUND INSIDE rawReply:",
            validJson
        );

        return validJson;
    }


    console.log(
        "No valid JSON in initial rawReply."
    );


    const baselineIds =
        new Set(
            Array.isArray(
                assistantBaseline?.assistantMessageIds
            )
                ? assistantBaseline.assistantMessageIds
                : []
        );


    console.log(
        "VISION baseline assistant selection IDs:",
        Array.from(
            baselineIds
        )
    );


    const timeoutMs = 30000;
    const pollMs = 500;
    const startedAt =
        Date.now();

    let pollNumber = 0;


    while (
        Date.now() - startedAt < timeoutMs
    ) {

        pollNumber++;


        try {

            const selectionNodes =
                Array.from(
                    document.querySelectorAll(
                        '[data-chatgpt-selection-message-id]'
                    )
                );


            const candidates = [];


            for (
                const selectionNode
                of selectionNodes
            ) {

                const messageId =
                    String(
                        selectionNode.getAttribute(
                            'data-chatgpt-selection-message-id'
                        )
                        || ""
                    ).trim();


                if (
                    !messageId
                    ||
                    baselineIds.has(
                        messageId
                    )
                ) {

                    continue;
                }


                const turn =
                    selectionNode.closest(
                        '[data-chatgpt-search-message-ids]'
                    );


                if (!turn) {
                    continue;
                }


                const markdownNode =
                    turn.querySelector(
                        '[data-markdown-text-style="assistant-message"]'
                    );


                const roleNode =
                    turn.querySelector(
                        '[data-conversation-role="assistant"]'
                    );


                if (
                    !markdownNode
                    &&
                    !roleNode
                ) {

                    continue;
                }


                const text =
                    getAssistantNodeText(
                        markdownNode || turn
                    );


                if (!text) {
                    continue;
                }


                candidates.push({

                    messageId,

                    text
                });
            }


            if (
                candidates.length > 0
            ) {

                const latest =
                    candidates[
                        candidates.length - 1
                    ];


                console.log(
                    `Vision NEW assistant message ${pollNumber}:`,
                    latest.messageId,
                    latest.text
                );


                for (
                    let i =
                        candidates.length - 1;
                    i >= 0;
                    i--
                ) {

                    validJson =
                        parseValidVisionJson(
                            candidates[i].text
                        );


                    if (validJson) {

                        console.log(
                            "VALID FINAL VISION JSON FOUND BY SELECTION MESSAGE ID:",
                            candidates[i].messageId,
                            validJson
                        );

                        return validJson;
                    }
                }

            } else if (
                pollNumber === 1
                ||
                pollNumber % 10 === 0
            ) {

                console.log(
                    `Vision DOM check ${pollNumber}: (no new assistant selection message id yet)`
                );
            }

        } catch (domError) {

            console.warn(
                "Vision DOM read failed:",
                domError
            );
        }


        await sleep(
            pollMs
        );
    }


    throw new Error(
        "Vision answer was not captured as a new assistant message within 30 seconds."
    );
}

// =========================================================
// COMMAND PROCESSOR
// =========================================================

async function processCommand(command) {

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


    if (command.id) {

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

        } catch (error) {

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


            await attachImageByPaste(
                file
            );


            console.log(
                "Image attached."
            );


            const assistantBaseline =
                getAssistantSnapshot();


            console.log(
                "Assistant baseline before Vision prompt:",
                assistantBaseline
            );


            await sendPromptDirectly(
                command.prompt
            );


            const rawReply = "";


            const replyText =
                await getReliableVisionReply(
                    rawReply,
                    assistantBaseline
                );


            console.log(
                "FINAL VISION REPLY:",
                replyText
            );


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

        } catch (error) {

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

        } catch (error) {


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