/**
 * SCX.AI Embedded Chatbot Widget Script
 * --------------------------------------
 * Add this 1-line script tag to ANY website to embed the chatbot:
 * <script src="http://127.0.0.1:8000/static/widget.js"></script>
 */
(function() {
    // Find script host server domain
    var currentScript = document.currentScript || (function() {
        var scripts = document.getElementsByTagName('script');
        return scripts[scripts.length - 1];
    })();
    
    var scriptUrl = new URL(currentScript.src);
    var serverHost = scriptUrl.origin; // e.g. http://127.0.0.1:8000 or production URL
    
    // Inject Widget Styles
    var style = document.createElement('style');
    style.innerHTML = `
        .scx-widget-trigger {
            position: fixed;
            bottom: 24px;
            right: 24px;
            width: 56px;
            height: 56px;
            border-radius: 50%;
            background: #10a37f;
            box-shadow: 0 4px 20px rgba(16, 163, 127, 0.4), 0 8px 16px rgba(0, 0, 0, 0.3);
            cursor: pointer;
            z-index: 999999;
            display: flex;
            align-items: center;
            justify-content: center;
            transition: transform 0.2s ease, background 0.2s ease;
            border: none;
            outline: none;
        }
        .scx-widget-trigger:hover {
            transform: scale(1.08);
            background: #1a7f64;
        }
        .scx-widget-trigger svg {
            width: 26px;
            height: 26px;
            fill: #ffffff;
        }
        .scx-widget-iframe-container {
            position: fixed;
            bottom: 92px;
            right: 24px;
            width: 400px;
            height: 620px;
            max-width: calc(100vw - 32px);
            max-height: calc(100vh - 120px);
            border-radius: 16px;
            box-shadow: 0 12px 40px rgba(0, 0, 0, 0.5), 0 0 0 1px rgba(255, 255, 255, 0.1);
            overflow: hidden;
            z-index: 999998;
            opacity: 0;
            transform: translateY(20px) scale(0.95);
            pointer-events: none;
            transition: opacity 0.25s ease, transform 0.25s ease;
        }
        .scx-widget-iframe-container.open {
            opacity: 1;
            transform: translateY(0) scale(1);
            pointer-events: auto;
        }
        .scx-widget-iframe {
            width: 100%;
            height: 100%;
            border: none;
        }
        @media (max-width: 480px) {
            .scx-widget-iframe-container {
                right: 12px;
                bottom: 84px;
                width: calc(100vw - 24px);
                height: calc(100vh - 100px);
            }
            .scx-widget-trigger {
                bottom: 16px;
                right: 16px;
            }
        }
    `;
    document.head.appendChild(style);

    // Create Trigger Button
    var trigger = document.createElement('button');
    trigger.className = 'scx-widget-trigger';
    trigger.title = 'Chat with SCX.AI Assistant';
    trigger.innerHTML = `
        <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
            <path d="M12 2C6.477 2 2 6.477 2 12c0 1.821.487 3.53 1.338 5.025L2.1 21.9a1 1 0 0 0 1.259 1.259l4.875-1.238A9.957 9.957 0 0 0 12 22c5.523 0 10-4.477 10-10S17.523 2 12 2zm0 18a7.954 7.954 0 0 1-4.084-1.12.997.997 0 0 0-.583-.141l-3.328.845.845-3.328a1 1 0 0 0-.141-.583A7.954 7.954 0 0 1 4 12c0-4.411 3.589-8 8-8s8 3.589 8 8-3.589 8-8 8z"/>
        </svg>
    `;
    document.body.appendChild(trigger);

    // Create Iframe Container
    var container = document.createElement('div');
    container.className = 'scx-widget-iframe-container';
    
    var iframe = document.createElement('iframe');
    iframe.className = 'scx-widget-iframe';
    iframe.src = serverHost + '/?embed=true';
    container.appendChild(iframe);
    
    document.body.appendChild(container);

    // Toggle Chat Popup
    var isOpen = false;
    trigger.onclick = function() {
        isOpen = !isOpen;
        if (isOpen) {
            container.classList.add('open');
            trigger.innerHTML = `
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
                    <path d="M19 6.41L17.59 5 12 10.59 6.41 5 5 6.41 10.59 12 5 17.59 6.41 19 12 13.41 17.59 19 19 17.59 13.41 12z"/>
                </svg>
            `;
        } else {
            container.classList.remove('open');
            trigger.innerHTML = `
                <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24">
                    <path d="M12 2C6.477 2 2 6.477 2 12c0 1.821.487 3.53 1.338 5.025L2.1 21.9a1 1 0 0 0 1.259 1.259l4.875-1.238A9.957 9.957 0 0 0 12 22c5.523 0 10-4.477 10-10S17.523 2 12 2zm0 18a7.954 7.954 0 0 1-4.084-1.12.997.997 0 0 0-.583-.141l-3.328.845.845-3.328a1 1 0 0 0-.141-.583A7.954 7.954 0 0 1 4 12c0-4.411 3.589-8 8-8s8 3.589 8 8-3.589 8-8 8z"/>
                </svg>
            `;
        }
    };
})();
