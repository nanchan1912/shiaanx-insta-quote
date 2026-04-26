const unique = (arr) => [...new Set(arr)];

const DEFAULT_MATERIAL = "Generic Aluminum";
const getCurrentBundle = (cutdata) => {
    const index = cutdata.selectedBundleIndex;
    return cutdata.bundles[index];
}

const toggleItem = (arr, item) => {
    const i = arr.indexOf(item);
    if (i == -1) {
        arr.push(item);
    } else {
        arr.splice(i, 1);
    }
    return arr;
}

class MaterialTab {
    constructor(parent) {
        this.cutdata = parent.cutdata;
        this.addBundleButton = document.getElementById('addBundleButton');
        this.removeSelectedButton = document.getElementById('removeSelectedBundleButton');
        this.submitEditButton = document.getElementById('submitEditButton');
        this.newBundleNameInput = document.getElementById('newBundleNameInput');
        this.bundleList = document.getElementById('itemsList');
        this.parent = parent;
        this.PRESET_PURPOSES = [
"tp_bore_finish",
"tp_face",
"tp_face_rough",
"tp_floor_finish",
"tp_wall_finish",
"tp_special",
"tp_surface",
"tp_traditional_rough",
"tp_adaptive_rough",
"tp_bore_rough",
"tp_slot",
"tp_drill",
"tp_engrave",


        ]

        this.attachEventListeners();
    }

    attachEventListeners() {
        this.addBundleButton.onclick = () => this.handleAddBundleButton();
        this.removeSelectedButton.onclick = () => this.handleRemoveSelectedButton();
        this.submitEditButton.onclick = () => this.handleSubmitEditButton();
    }
    handleAddBundleButton() {
        // TODO replace this by copy
        const name = this.newBundleNameInput.value.trim();
        this.newBundleNameInput.value = ''; // Clear input field
        if (!name) {
            alert("Please enter a valid material name.");
            return;
        }
        let badChar = this.firstInvalidNameChar(name);
        if (badChar) {
            alert(`Invalid character "${badChar}" in material name.`);
            return;
        }
        let existsAlready = false;
        this.cutdata.bundles.forEach(bundle => {
            if (bundle.name == name) {
                existsAlready = true;
            }
        })
        if (existsAlready) {
            alert(`Cutting bundle "${name}" already exists.`);
            return;
        };

        const default_bundle = this.getBundleByName(DEFAULT_MATERIAL);
        const bundle = JSON.parse(JSON.stringify(default_bundle)); // deep copy
        bundle.name = name;
        this.addBundle(bundle);
        this.render();
        this.sendDataToFusion("MaterialTab_saveData", this.cutdata);
    };

    handleRemoveSelectedButton() {
        const index = this.cutdata.selectedBundleIndex;
        const item = this.cutdata.bundles[index];
        if (item.name == DEFAULT_MATERIAL) {
            alert("Removing the default material is not allowed.");
            return;
        }
        this.cutdata.bundles.splice(index, 1); // Remove item from array
        this.selectBundleByName(DEFAULT_MATERIAL);
        this.render(); // Refresh the list
        this.sendDataToFusion("MaterialTab_saveData", this.cutdata);
    };

    handleSubmitEditButton() {
        const index = this.cutdata.selectedBundleIndex;
        if (this.cutdata.bundles[index].name == DEFAULT_MATERIAL) {
            alert("Editing the default material is not allowed.");
            this.render();
            return;
        }
        let invalid_preset_msg = null;
        this.PRESET_PURPOSES.forEach((purpose) => {
            const b = this.getContentBoxFromPurpose(purpose);
            const preset_name_candidates = b.value.split(",").map((s) => s.trim());
            let preset_names = [];
            preset_name_candidates.forEach((name) => {
                const bad_char = this.firstInvalidNameChar(name);
                if (bad_char) {
                    invalid_preset_msg = `Invalid character "${bad_char}" in preset name "${name}" for purpose "${purpose}".`;
                    return;
                };
                if (!name) {
                    invalid_preset_msg = `Got an empty preset name for purpose "${purpose}".`;
                    return;
                };
                preset_names.push(name);
            })
            preset_names = [...new Set(preset_names)]; // deduplicate
            this.cutdata.bundles[index].preset_naming[purpose] = preset_names
        })
        if (invalid_preset_msg) {
            alert(`Found invalid preset names. These will be removed: ${invalid_preset_msg}`);
        }
        this.render(); // Refresh the list to reflect changes
        this.sendDataToFusion("MaterialTab_saveData", this.cutdata);
    };

    firstInvalidNameChar(name) {
        const bad_chars = /[^A-Za-z0-9 _-]/;
        const match = name.match(bad_chars);
        if (match) {
            return match[0]; // Returns the first match (invalid character)
        } else {
            return null; // No invalid characters found
        }
    };
    findBundleIndexByName(name) {
        const bundles = this.cutdata.bundles;
        for (let i = 0; i < bundles.length; i++) {
            if (bundles[i].name === name) {
                return i;
            }
        }
        throw new Error(`No item with name = ${name}`);
    };
    getBundleByName(name) {
        const i = this.findBundleIndexByName(name)
        return this.cutdata.bundles[i];
    };
    selectBundleByName(name) {
        const i = this.findBundleIndexByName(name);
        this.cutdata.selectedBundleIndex = i;
    };

    pushUpdateSelectedBundleIndex(i) {
        this.cutdata.selectedBundleIndex = i;
        this.render();
    }
    getContentBoxFromPurpose(purpose) {
        const name = "contentBox_" + purpose;
        document.getElementById('contentBox_tp_face');
        return document.getElementById(name);
    };

    render() {
        this.bundleList.innerHTML = ''; // Clear current list
        this.cutdata.bundles.forEach((bundle, index) => {
            const li = document.createElement('li');
            li.textContent = bundle.name;
            li.title = "Click to select this material."
            if (index == this.cutdata.selectedBundleIndex) {
                li.classList.add('selected');
                this.PRESET_PURPOSES.forEach((purpose) => {
                    let b = this.getContentBoxFromPurpose(purpose);
                    b.value = bundle.preset_naming[purpose].join(', ');
                })
                document.getElementById("titlePresetNamesAndPriorities").innerHTML = `Selected Material: "${bundle.name}"`
            } else {
                li.classList.remove('selected');
            }
            li.onclick = () => this.handleMaterialClick(bundle.name);
            this.bundleList.appendChild(li);
        });
    }

    handleMaterialClick(materialName) {
        this.selectBundleByName(materialName);
        this.render()
        this.sendDataToFusion("MaterialTab_saveData", this.cutdata);
        this.parent.receiveMsg({
            "subtypekey": "selectedBundleIndexSetFromMaterialTab",
            "selectedBundleIndex": this.cutdata.selectedBundleIndex,
        });
    }

    sendDataToFusion(eventName, data) {
        adsk
            .fusionSendData(eventName, JSON.stringify(data));
    }

    addBundle(bundle) {
        this.cutdata.bundles.push(bundle);
        this.cutdata.selectedBundleIndex = this.cutdata.bundles.length - 1;
    }

}

class ToollibForest {
    constructor(parent, container) {
        this.container = container;
        this.parent = parent;
        this.cutdata = parent.cutdata;
        this.nodeByUrl = {};
        this.buildNodesByUrl(this.cutdata.toollibForest);
    }

    buildNodesByUrl(nodes) {
        nodes.forEach((node) => {
            this.nodeByUrl[node.url] = node;
            if (node.kind == "branch") {
                this.buildNodesByUrl(node.children);
            };
        })
    }

    onToggleExpandBranch(node) {
        node.isExpanded = !node.isExpanded;
        this.render(); // Re-render the whole tree
    }

    collectNodesWithoutPresetNames(nodes) {
        return nodes.filter(
            (node) => {
                return node.preset_names === undefined; // or rather undefined
            }
        );
    }

    async requestPresetNamesAsync(nodes) {
        console.log("before requestPresetNamesAsync", this.getSelectedNodes());
        const nodes_without_preset_names = this.collectNodesWithoutPresetNames(nodes);
        if (nodes_without_preset_names.length > 0) {
            const urls = nodes_without_preset_names.map((node) => node.url);
            const result = await adsk.fusionSendData("ToolLibTab_getPresetNames", JSON.stringify(urls));
            const preset_names_list = JSON.parse(result);
            if (nodes_without_preset_names.length !== preset_names_list.length) {
                alter(
                    `Got ${preset_names_list.length} preset names for ${nodes_without_preset_names.length} nodes.`
                );
            };
            for (let i = 0; i < nodes_without_preset_names.length; i++) {
                nodes_without_preset_names[i].preset_names = preset_names_list[i];
            };
        };
        console.log("after requestPresetNamesAsync", this.getSelectedNodes());
        return this.collectPresetNamesIfAllAvailable(nodes);
    }

    collectPresetNamesIfAllAvailable(nodes) {
        let preset_names = [];
        for (let i = 0; i < nodes.length; i++) {
            const node = nodes[i];
            if (node.preset_names == undefined) {
                return null;
            } else {
                preset_names.push(...node.preset_names);
            }
        }
        preset_names = unique(preset_names);
        preset_names.sort();
        console.log("preset_names", preset_names);
        return preset_names;
    }

    onToggleSelectLeaf(node, checkbox) {
        const bundle = getCurrentBundle(this.cutdata);
        toggleItem(bundle.selectedToollibURLs, node.url);
        adsk
            .fusionSendData("ToolLibTab_selectionChanged", JSON.stringify(bundle.selectedToollibURLs));
        this.render();
    }

    renderLeafNode(node, parentElement) {
        const listItem = document.createElement('li');
        const checkbox = document.createElement('input');
        const bundle = getCurrentBundle(this.cutdata);
        checkbox.type = 'checkbox';
        const isSelected = bundle.selectedToollibURLs.includes(node.url);
        checkbox.checked = isSelected;
        checkbox.className = 'checkbox';
        checkbox.onchange = () => this.onToggleSelectLeaf(node, checkbox);
        listItem.appendChild(checkbox);
        const textNode = document.createTextNode(node.name);
        listItem.appendChild(textNode);
        parentElement.appendChild(listItem);
    }

    renderBranchNode(node, parentElement) {
        const listItem = document.createElement('li');
        const expandButton = document.createElement('span');
        expandButton.textContent = node.isExpanded ? '[-] ' : '[+] ';
        expandButton.onclick = () => this.onToggleExpandBranch(node);
        listItem.appendChild(expandButton);
        const textNode = document.createTextNode(node.name);
        listItem.appendChild(textNode);
        if (node.isExpanded && node.children) {
            this.renderChildren(node.children, listItem);
        }
        parentElement.appendChild(listItem);
    }

    renderChildren(children, parentElement) {
        const list = document.createElement('ul');
        list.className = 'tree-list';
        children.forEach(childNode => this.renderNode(childNode, list));
        parentElement.appendChild(list);
    }


    renderNode(node, parentElement) {
        if (node.kind == 'branch') {
            this.renderBranchNode(node, parentElement);
        } else if (node.kind == 'leaf') {
            this.renderLeafNode(node, parentElement);
        } else {
            throw new Error(`Unreachable: ${node.kind}`);
        }
    }

    getSelectedNodes() {
        const bundle = getCurrentBundle(this.cutdata);
        return bundle.selectedToollibURLs.map(url => this.nodeByUrl[url]);
    }

    renderSelection() {
        const container = document.createElement('div');
        // container.innerHTML = '';
        container.id = 'checkedItemsList';
        const nodes = this.getSelectedNodes();

        if (nodes.length == 0) {
            container.textContent =
                "No tool libraries selected. Please select at least one.";
        } else {
            if (nodes.length == 1) {
                container.textContent = `One tool library selected:`;
            } else {
                container.textContent = `${nodes.length} tool libraries selected:`;
            }

            const list = document.createElement('ul');
            container.appendChild(list);
            nodes.forEach(node => {
                const listItem = document.createElement('li');
                const textNode = document.createTextNode(node.name);
                listItem.appendChild(textNode);
                list.appendChild(listItem);
            });
        }
        this.container.appendChild(container);
    }

    renderPresetNamesIfAvailable() {
        const nodes = this.getSelectedNodes();
        const presetNames = this.collectPresetNamesIfAllAvailable(nodes);
        const presetNamesContainer = document.createElement('div');
        presetNamesContainer.id = 'presetNamesContainer';
        if (presetNames == null) {
            presetNamesContainer.textContent = "";
            const showPresetNamesButton = document.createElement('button');
            showPresetNamesButton.textContent = "Gather Preset Names";
            showPresetNamesButton.onclick = () => this.handleShowPresetNamesClick();
            presetNamesContainer.appendChild(showPresetNamesButton);
        } else {
            const list = document.createElement('ul');
            presetNamesContainer.textContent = "The following presets are available with the tool libraries selected:"
            presetNames.forEach(name => {
                const listItem = document.createElement('li');
                const textNode = document.createTextNode(name);
                listItem.appendChild(textNode);
                list.appendChild(listItem);
            });
            console.log("presetNames", presetNames);
            presetNamesContainer.appendChild(list);
        }
        this.container.appendChild(presetNamesContainer);
    }

    async handleShowPresetNamesClick() {
        const nodes = this.getSelectedNodes();
        await this.requestPresetNamesAsync(nodes);
        this.render();
    }

    render() {
        this.container.innerHTML = ''; // Clear the container
        const bundle = getCurrentBundle(this.cutdata);
        if (bundle.name != DEFAULT_MATERIAL) {
            const list = document.createElement('ul');
            list.className = 'tree-list';
            this.renderChildren(this.cutdata.toollibForest, list);
            this.container.appendChild(list);
        }

        this.renderSelection();

        this.renderPresetNamesIfAvailable();
    }
}

class CuttingConfigTab {
    constructor(cutdata) {
        this.cutdata = cutdata;
        const forestContainer = document.getElementById("toollibForestContainer");
        this.forestTab = new ToollibForest(this, forestContainer);
        this.materialTab = new MaterialTab(this);
    }

    receiveMsg(msg) {
        const subtypekey = msg.subtypekey
        if (subtypekey == "selectedBundleIndexSetFromSetupTab") {
            this.cutdata.selectedBundleIndex = msg.selectedBundleIndex;
            this.render();
        } else if (subtypekey == "selectedBundleIndexSetFromMaterialTab") {
            const bundle = getCurrentBundle(this.cutdata);
            this.forestTab.render();
        } else {
            alert(`TODO msg subtypekey ${subtypekey}`);
        }
    }

    render() {
        this.forestTab.render();
        this.materialTab.render();
    }
}

document.addEventListener('DOMContentLoaded', () => {
    let adskWaiter = setInterval(() => {
        if (window.adsk) {
            clearInterval(adskWaiter);
            adsk
                .fusionSendData("CuttingConfigTab_getInitialData", "")
                .then((result) => {
                    let initialData = JSON.parse(result);
                    const cuttingConfigTab = new CuttingConfigTab(initialData);
                    window.fusionJavaScriptHandler = {
                        handle: (actionString, dataString) => {
                            const msg = JSON.parse(dataString);
                            cuttingConfigTab.receiveMsg(msg)
                        }
                    };
                    cuttingConfigTab.render();
                });
        }
    });
});