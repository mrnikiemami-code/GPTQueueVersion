console.log("ChatGPT Bot background service worker started.");

chrome.runtime.onMessage.addListener(
    (message, sender, sendResponse) => {

        if (!message) {
            return;
        }

        if (message.type === "bridgeFetch") {

            handleBridgeFetch(message)
                .then(result => {

                    sendResponse({
                        success: true,
                        data: result
                    });

                })
                .catch(error => {

                    console.error(
                        "BRIDGE FETCH ERROR:",
                        error
                    );

                    sendResponse({
                        success: false,
                        error: String(error)
                    });

                });

            return true;
        }

        if (message.type === "waitForVisionDownload") {

            waitForVisionDownload(
                message.fileName || "vision-result.json",
                Number(message.timeoutMs || 80000)
            )
                .then(result => {

                    sendResponse({
                        success: true,
                        data: result
                    });

                })
                .catch(error => {

                    console.error(
                        "VISION DOWNLOAD WATCH ERROR:",
                        error
                    );

                    sendResponse({
                        success: false,
                        error: String(error)
                    });

                });

            return true;
        }
    }
);


async function handleBridgeFetch(message) {

    const options = {
        method: message.method || "GET",

        headers: {
            "Content-Type": "application/json"
        }
    };


    if (message.body !== undefined) {

        options.body = JSON.stringify(
            message.body
        );
    }


    const response = await fetch(
        message.url,
        options
    );


    if (!response.ok) {

        throw new Error(
            `HTTP ${response.status}`
        );
    }


    return await response.json();
}

function _matchesVisionFileName(
    downloadedPath,
    expectedFileName
) {

    const normalizedPath =
        String(
            downloadedPath || ""
        )
            .replace(
                /\\/g,
                "/"
            )
            .toLowerCase();


    const normalizedExpected =
        String(
            expectedFileName || "vision-result.json"
        )
            .toLowerCase();


    const slashIndex =
        normalizedPath.lastIndexOf(
            "/"
        );


    const baseName =
        slashIndex >= 0
            ? normalizedPath.slice(
                slashIndex + 1
            )
            : normalizedPath;


    if (
        baseName ===
        normalizedExpected
    ) {

        return true;
    }


    const dotIndex =
        normalizedExpected.lastIndexOf(
            "."
        );


    const stem =
        dotIndex >= 0
            ? normalizedExpected.slice(
                0,
                dotIndex
            )
            : normalizedExpected;


    const extension =
        dotIndex >= 0
            ? normalizedExpected.slice(
                dotIndex
            )
            : "";


    return (
        baseName.startsWith(
            stem + " ("
        )
        &&
        baseName.endsWith(
            ")" + extension
        )
    );
}


function waitForVisionDownload(
    expectedFileName,
    timeoutMs
) {

    return new Promise(
        (
            resolve,
            reject
        ) => {

            let settled = false;
            let downloadId = null;


            const cleanup = () => {

                chrome.downloads.onCreated.removeListener(
                    onCreated
                );

                chrome.downloads.onChanged.removeListener(
                    onChanged
                );

                clearTimeout(
                    timeoutHandle
                );
            };


            const finishSuccess = (
                item
            ) => {

                if (settled) {
                    return;
                }


                settled = true;
                cleanup();

                resolve({
                    id:
                        item.id,

                    fileName:
                        item.filename,

                    finalUrl:
                        item.finalUrl
                        || item.url
                        || "",

                    state:
                        item.state
                        || "complete"
                });
            };


            const finishError = (
                message
            ) => {

                if (settled) {
                    return;
                }


                settled = true;
                cleanup();

                reject(
                    new Error(
                        message
                    )
                );
            };


            const readCompletedItem = (
                id
            ) => {

                chrome.downloads.search(
                    {
                        id
                    },
                    items => {

                        if (
                            chrome.runtime.lastError
                        ) {

                            finishError(
                                chrome.runtime.lastError.message
                            );

                            return;
                        }


                        const item =
                            Array.isArray(
                                items
                            )
                            &&
                            items.length > 0
                                ? items[0]
                                : null;


                        if (!item) {

                            finishError(
                                "Completed Vision download could not be resolved."
                            );

                            return;
                        }


                        if (
                            item.state ===
                            "complete"
                        ) {

                            finishSuccess(
                                item
                            );
                        }
                    }
                );
            };


            const onCreated = (
                item
            ) => {

                if (
                    !item
                    ||
                    !_matchesVisionFileName(
                        item.filename,
                        expectedFileName
                    )
                ) {

                    return;
                }


                downloadId =
                    item.id;


                console.log(
                    "VISION download created:",
                    {
                        id:
                            item.id,

                        filename:
                            item.filename
                    }
                );


                if (
                    item.state ===
                    "complete"
                ) {

                    finishSuccess(
                        item
                    );
                }
            };


            const onChanged = (
                delta
            ) => {

                if (
                    downloadId === null
                    ||
                    !delta
                    ||
                    delta.id !==
                    downloadId
                ) {

                    return;
                }


                if (
                    delta.error
                    &&
                    delta.error.current
                ) {

                    finishError(
                        "Vision result download failed: "
                        + delta.error.current
                    );

                    return;
                }


                if (
                    delta.state
                    &&
                    delta.state.current ===
                    "complete"
                ) {

                    readCompletedItem(
                        downloadId
                    );
                }
            };


            chrome.downloads.onCreated.addListener(
                onCreated
            );

            chrome.downloads.onChanged.addListener(
                onChanged
            );


            const timeoutHandle =
                setTimeout(
                    () => {

                        finishError(
                            "vision-result.json was not downloaded within the allowed time."
                        );
                    },
                    Math.max(
                        1000,
                        timeoutMs
                    )
                );
        }
    );
}
