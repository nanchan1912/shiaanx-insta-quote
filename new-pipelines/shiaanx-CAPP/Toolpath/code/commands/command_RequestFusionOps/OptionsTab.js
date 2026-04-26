// OptionsTab.js - Handles the options panel UI interactions

let optionsSchema = [];
let optionsValues = {};
let initialValues = {};
let availableFolders = [];
let availableFiles = [];

// Group options by category (top-level) and group (sub-level)
function groupOptionsByCategoryAndGroup(schema) {
    const categories = {};
    for (const opt of schema) {
        const category = opt.category || "General";
        const group = opt.group || "General";

        if (!categories[category]) {
            categories[category] = {};
        }
        if (!categories[category][group]) {
            categories[category][group] = [];
        }
        categories[category][group].push(opt);
    }
    return categories;
}

// Create HTML for a boolean (checkbox) option
function createCheckboxHTML(opt) {
    const value = optionsValues[opt.key];
    const id = `opt_${opt.key}`;
    const checked = value ? "checked" : "";

    return `
        <div class="option-row" data-key="${opt.key}">
            <div class="checkbox-wrapper">
                <input type="checkbox" id="${id}" data-key="${opt.key}" ${checked}>
                <label for="${id}">${opt.label}</label>
            </div>
            <div class="option-tooltip">${opt.tooltip}</div>
        </div>
    `;
}

// Create HTML for a string (text input) option
function createTextInputHTML(opt) {
    const value = optionsValues[opt.key] || "";
    const id = `opt_${opt.key}`;

    return `
        <div class="option-row" data-key="${opt.key}">
            <div class="text-input-wrapper">
                <label for="${id}">${opt.label}</label>
                <input type="text" id="${id}" data-key="${opt.key}" value="${escapeHtml(value)}">
            </div>
            <div class="option-tooltip">${opt.tooltip}</div>
        </div>
    `;
}

// Create HTML for a folder picker (dropdown) option
function createFolderPickerHTML(opt) {
    const value = optionsValues[opt.key] || "";
    const id = `opt_${opt.key}`;

    let optionsHtml = '<option value="">-- Select a folder --</option>';
    for (const folder of availableFolders) {
        const selected = folder === value ? "selected" : "";
        optionsHtml += `<option value="${escapeHtml(folder)}" ${selected}>${escapeHtml(folder)}</option>`;
    }

    return `
        <div class="option-row" data-key="${opt.key}">
            <div class="select-wrapper">
                <label for="${id}">${opt.label}</label>
                <select id="${id}" data-key="${opt.key}" data-type="folder_picker">
                    ${optionsHtml}
                </select>
            </div>
            <div class="option-tooltip">${opt.tooltip}</div>
        </div>
    `;
}

// Create HTML for a file picker (dropdown) option
function createFilePickerHTML(opt) {
    const value = optionsValues[opt.key] || "";
    const id = `opt_${opt.key}`;

    let optionsHtml = '<option value="">-- Select a file --</option>';
    for (const file of availableFiles) {
        const selected = file === value ? "selected" : "";
        optionsHtml += `<option value="${escapeHtml(file)}" ${selected}>${escapeHtml(file)}</option>`;
    }

    return `
        <div class="option-row" data-key="${opt.key}">
            <div class="select-wrapper">
                <label for="${id}">${opt.label}</label>
                <select id="${id}" data-key="${opt.key}" data-type="file_picker" data-folder-key="${opt.folder_key || ''}">
                    ${optionsHtml}
                </select>
            </div>
            <div class="option-tooltip">${opt.tooltip}</div>
        </div>
    `;
}

// Escape HTML special characters
function escapeHtml(text) {
    const div = document.createElement('div');
    div.textContent = text;
    return div.innerHTML;
}

// Create HTML for a single option based on its type
function createOptionHTML(opt) {
    if (opt.type === "bool") {
        return createCheckboxHTML(opt);
    } else if (opt.type === "string") {
        return createTextInputHTML(opt);
    } else if (opt.type === "int" || opt.type === "number") {
        return createTextInputHTML(opt);
    } else if (opt.type === "folder_picker") {
        return createFolderPickerHTML(opt);
    } else if (opt.type === "file_picker") {
        return createFilePickerHTML(opt);
    }
    return "";
}

// Render a single group with its options
function renderGroup(groupName, options) {
    // Find ALL boolean "enable_*" options - these are top-level siblings
    const enableOptions = options.filter(
        (opt) => opt.type === "bool" && opt.key.startsWith("enable_")
    );
    // Dependent options are everything that's not an enable option
    const dependentOptions = options.filter(
        (opt) => !(opt.type === "bool" && opt.key.startsWith("enable_"))
    );

    let html = `<div class="group" data-group="${groupName}">`;
    html += `<div class="group-header">${groupName}</div>`;

    if (enableOptions.length > 0) {
        // Render all enable options as top-level siblings
        for (const enableOpt of enableOptions) {
            html += createOptionHTML(enableOpt);
        }

        // Render dependent options in a container controlled by the first enable option
        if (dependentOptions.length > 0 && enableOptions.length === 1) {
            const firstEnableKey = enableOptions[0].key;
            const isEnabled = optionsValues[firstEnableKey];
            const disabledClass = isEnabled ? "" : "disabled";
            html += `<div class="dependent-fields ${disabledClass}" data-depends-on="${firstEnableKey}">`;
            for (const opt of dependentOptions) {
                html += createOptionHTML(opt);
            }
            html += `</div>`;
        } else if (dependentOptions.length > 0) {
            // Multiple enable options - render dependent options without dependency
            for (const opt of dependentOptions) {
                html += createOptionHTML(opt);
            }
        }
    } else {
        // No enable option, just render all options
        for (const opt of options) {
            html += createOptionHTML(opt);
        }
    }

    html += `</div>`;
    return html;
}

// Render all options grouped by category and group
function renderOptions() {
    const container = document.getElementById("optionsContent");
    const categories = groupOptionsByCategoryAndGroup(optionsSchema);

    let html = "";

    for (const [categoryName, groups] of Object.entries(categories)) {
        html += `<div class="category" data-category="${categoryName}">`;
        html += `<div class="category-header">${categoryName}</div>`;
        html += `<div class="category-content">`;

        for (const [groupName, options] of Object.entries(groups)) {
            html += renderGroup(groupName, options);
        }

        html += `</div>`;
        html += `</div>`;
    }

    container.innerHTML = html;

    // Attach event listeners
    attachEventListeners();
}

// Attach event listeners to inputs
function attachEventListeners() {
    // Checkbox change handlers
    const checkboxes = document.querySelectorAll('input[type="checkbox"]');
    for (const cb of checkboxes) {
        cb.addEventListener("change", onCheckboxChange);
    }

    // Text input change handlers
    const textInputs = document.querySelectorAll('input[type="text"]');
    for (const input of textInputs) {
        input.addEventListener("input", onTextInputChange);
    }

    // Select (dropdown) change handlers
    const selects = document.querySelectorAll('select');
    for (const select of selects) {
        select.addEventListener("change", onSelectChange);
    }

    // Save button
    document.getElementById("saveButton").addEventListener("click", saveOptions);

    // Reset button
    document.getElementById("resetButton").addEventListener("click", resetOptions);
}

// Handle checkbox changes
function onCheckboxChange(event) {
    const key = event.target.dataset.key;
    const value = event.target.checked;
    optionsValues[key] = value;

    // Update dependent fields visibility
    const dependentContainer = document.querySelector(
        `[data-depends-on="${key}"]`
    );
    if (dependentContainer) {
        if (value) {
            dependentContainer.classList.remove("disabled");
            // Enable inputs
            const inputs = dependentContainer.querySelectorAll("input");
            for (const input of inputs) {
                input.disabled = false;
            }
        } else {
            dependentContainer.classList.add("disabled");
            // Disable inputs
            const inputs = dependentContainer.querySelectorAll("input");
            for (const input of inputs) {
                input.disabled = true;
            }
        }
    }
}

// Handle text input changes
function onTextInputChange(event) {
    const key = event.target.dataset.key;
    let value = event.target.value;

    // Find the option schema to check type
    const opt = optionsSchema.find((o) => o.key === key);
    if (opt && (opt.type === "int" || opt.type === "number")) {
        value = parseInt(value, 10) || 0;
    }

    optionsValues[key] = value;
}

// Handle select (dropdown) changes
function onSelectChange(event) {
    const key = event.target.dataset.key;
    const value = event.target.value;
    const type = event.target.dataset.type;

    optionsValues[key] = value;

    // If this is a folder picker, refresh the file lists for dependent file pickers
    if (type === "folder_picker") {
        refreshFilesForFolder(key, value);
    }
}

// Refresh file picker options when folder changes
async function refreshFilesForFolder(folderKey, folderName) {
    if (!folderName) {
        availableFiles = [];
        updateFilePickerOptions();
        return;
    }

    try {
        const result = await adsk.fusionSendData("OptionsTab_getFilesInFolder", folderName);
        availableFiles = JSON.parse(result);
        updateFilePickerOptions();
    } catch (e) {
        console.error("Error fetching files:", e);
        availableFiles = [];
        updateFilePickerOptions();
    }
}

// Update all file picker dropdowns with new options
function updateFilePickerOptions() {
    const fileSelects = document.querySelectorAll('select[data-type="file_picker"]');
    for (const select of fileSelects) {
        const currentValue = select.value;
        let optionsHtml = '<option value="">-- Select a file --</option>';

        for (const file of availableFiles) {
            const selected = file === currentValue ? "selected" : "";
            optionsHtml += `<option value="${escapeHtml(file)}" ${selected}>${escapeHtml(file)}</option>`;
        }

        select.innerHTML = optionsHtml;

        // If current value is no longer valid, clear it
        if (currentValue && !availableFiles.includes(currentValue)) {
            select.value = "";
            optionsValues[select.dataset.key] = "";
        }
    }
}

// Save options to Python backend
function saveOptions() {
    adsk.fusionSendData("OptionsTab_saveOptions", JSON.stringify(optionsValues)).then(
        (result) => {
            const response = JSON.parse(result);
            if (response.success) {
                // Update initial values to current values
                initialValues = { ...optionsValues };
                if (response.requires_restart) {
                    showStatus("Options saved. Restart Fusion 360 for some changes to take effect.", "warning");
                } else {
                    showStatus("Options saved successfully", "success");
                }
            } else {
                showStatus("Failed to save options", "error");
            }
        }
    );
}

// Reset options to initial values
function resetOptions() {
    optionsValues = { ...initialValues };
    renderOptions();
    updateDependentFieldsState();
    showStatus("Options reset to last saved values", "success");
}

// Update the enabled/disabled state of dependent fields
function updateDependentFieldsState() {
    const dependentContainers = document.querySelectorAll("[data-depends-on]");
    for (const container of dependentContainers) {
        const dependsOnKey = container.dataset.dependsOn;
        const isEnabled = optionsValues[dependsOnKey];

        if (isEnabled) {
            container.classList.remove("disabled");
            const inputs = container.querySelectorAll("input");
            for (const input of inputs) {
                input.disabled = false;
            }
        } else {
            container.classList.add("disabled");
            const inputs = container.querySelectorAll("input");
            for (const input of inputs) {
                input.disabled = true;
            }
        }
    }
}

// Show status message
function showStatus(message, type) {
    const statusEl = document.getElementById("statusMessage");
    statusEl.textContent = message;
    statusEl.className = `status-message ${type}`;

    // Auto-hide after 3 seconds
    setTimeout(() => {
        statusEl.className = "status-message";
    }, 3000);
}

// Initialize the options panel
function initialize() {
    adsk.fusionSendData("OptionsTab_getInitialData", "").then((result) => {
        try {
            const data = JSON.parse(result);
            optionsSchema = data.schema;
            optionsValues = data.values;
            initialValues = { ...data.values };
            availableFolders = data.folders || [];
            availableFiles = data.files || [];

            renderOptions();
            updateDependentFieldsState();
        } catch (e) {
            console.error("Error parsing options data:", e);
            document.getElementById("optionsContent").innerHTML =
                '<div class="group"><div class="group-header">Error</div><p>Failed to load options: ' + e.message + '</p></div>';
        }
    }).catch((error) => {
        console.error("Error fetching options:", error);
        document.getElementById("optionsContent").innerHTML =
            '<div class="group"><div class="group-header">Error</div><p>Failed to fetch options from Fusion.</p></div>';
    });
}

// Start initialization when DOM is ready - wait for adsk object to be injected
document.addEventListener('DOMContentLoaded', () => {
    let adskWaiter = setInterval(() => {
        if (window.adsk) {
            clearInterval(adskWaiter);
            initialize();
        }
    }, 100);

    // Timeout after 10 seconds
    setTimeout(() => {
        clearInterval(adskWaiter);
        if (!window.adsk) {
            console.error("Fusion API not available after timeout");
            document.getElementById("optionsContent").innerHTML =
                '<div class="group"><div class="group-header">Error</div><p>Fusion API not available. Please ensure this is running inside Fusion 360.</p></div>';
        }
    }, 10000);
});
