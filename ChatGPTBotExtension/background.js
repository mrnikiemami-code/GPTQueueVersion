console.log("ChatGPT Bot background service worker started.");

chrome.runtime.onMessage.addListener(
    (message, sender, sendResponse) => {

        if (!message || message.type !== "bridgeFetch") {
            return;
        }

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