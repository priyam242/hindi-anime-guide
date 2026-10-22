// Admin JS - Hindi Anime Guide Premium Admin Panel

// Dark mode toggle
function toggleDarkMode() {
    document.body.classList.toggle('dark-mode');
    const isDark = document.body.classList.contains('dark-mode');
    document.cookie = `dark_mode=${isDark ? '1' : '0'}; path=/; max-age=31536000`;
    const icon = document.getElementById('dark-mode-icon');
    if (icon) {
        icon.textContent = isDark ? '☀️' : '🌙';
    }
}

// Dropdown toggle
function toggleDropdown(id) {
    const el = document.getElementById(id);
    if (!el) return;
    const isHidden = el.classList.contains('hidden');
    // Close other dropdowns first
    document.querySelectorAll('.dropdown-menu').forEach(m => {
        if (m.id !== id) m.classList.add('hidden');
    });
    if (isHidden) {
        el.classList.remove('hidden');
    } else {
        el.classList.add('hidden');
    }
}

// Close dropdowns when clicking outside
document.addEventListener('click', function(e) {
    if (!e.target.closest('.dropdown-container')) {
        document.querySelectorAll('.dropdown-menu').forEach(m => m.classList.add('hidden'));
    }
});

// Sidebar toggle (mobile / responsive)
function toggleSidebar() {
    const sidebar = document.getElementById('sidebar');
    const overlay = document.getElementById('sidebar-overlay');
    if (sidebar) {
        sidebar.classList.toggle('collapsed');
        sidebar.classList.toggle('mobile-open');
    }
    if (overlay) {
        overlay.classList.toggle('hidden');
    }
}

// Avatar upload preview
function previewAvatar(input) {
    if (input.files && input.files[0]) {
        const file = input.files[0];
        if (file.size > 2 * 1024 * 1024) {
            alert('File too large! Maximum allowed avatar size is 2MB.');
            input.value = '';
            return;
        }
        const reader = new FileReader();
        reader.onload = function(e) {
            const previewImg = document.getElementById('avatar-preview-img');
            const previewInitials = document.getElementById('avatar-preview-initials');
            if (previewImg) {
                previewImg.src = e.target.result;
                previewImg.classList.remove('hidden');
            }
            if (previewInitials) {
                previewInitials.classList.add('hidden');
            }
        };
        reader.readAsDataURL(file);
    }
}

// Password strength meter
function checkPasswordStrength(password) {
    const meter = document.getElementById('password-strength');
    const label = document.getElementById('strength-label');
    if (!meter) return;
    if (!password) {
        meter.style.width = '0%';
        if (label) label.textContent = '';
        return;
    }
    let strength = 0;
    if (password.length >= 8) strength++;
    if (/[A-Z]/.test(password)) strength++;
    if (/[a-z]/.test(password)) strength++;
    if (/\d/.test(password)) strength++;
    if (/[@$!%*?&#^()_+\-=\[\]{}|;:,.<>?]/.test(password)) strength++;

    const widths = ['10%', '25%', '50%', '75%', '100%'];
    const colors = ['var(--strength-weak)', 'var(--strength-weak)', 'var(--strength-fair)', 'var(--strength-good)', 'var(--strength-strong)'];
    const labels = ['Too Short', 'Weak', 'Fair', 'Good', 'Strong'];

    const index = Math.max(0, Math.min(strength - 1, 4));
    meter.style.width = widths[index];
    meter.style.backgroundColor = colors[index];
    if (label) {
        label.textContent = labels[index];
        label.style.color = colors[index];
    }
}

// Tab navigation for settings
function switchTab(tabId) {
    document.querySelectorAll('.tab-content').forEach(c => c.classList.remove('active'));
    document.querySelectorAll('.tab-btn').forEach(b => b.classList.remove('active'));
    
    const targetContent = document.getElementById('tab-' + tabId);
    const targetBtn = document.getElementById('btn-' + tabId);
    if (targetContent) targetContent.classList.add('active');
    if (targetBtn) targetBtn.classList.add('active');
    
    // Save active tab in hash or query
    if (history.replaceState) {
        history.replaceState(null, null, '#' + tabId);
    }
}

// Real-time table search filter
function filterTable(inputId, tableId) {
    const input = document.getElementById(inputId);
    if (!input) return;
    const filter = input.value.toLowerCase();
    const table = document.getElementById(tableId);
    if (!table) return;
    const rows = table.querySelectorAll('tbody tr');
    let visibleCount = 0;
    rows.forEach(row => {
        if (row.classList.contains('no-filter')) return;
        const text = row.innerText.toLowerCase();
        const matches = text.includes(filter);
        row.style.display = matches ? '' : 'none';
        if (matches) visibleCount++;
    });
    const emptyRow = document.getElementById(tableId + '-empty');
    if (emptyRow) {
        emptyRow.style.display = visibleCount === 0 ? '' : 'none';
    }
}

// Client-side table sort
function sortTable(tableId, colIndex, isNumeric = false) {
    const table = document.getElementById(tableId);
    if (!table) return;
    const tbody = table.querySelector('tbody');
    const rows = Array.from(tbody.querySelectorAll('tr:not(.no-sort)'));
    const currentSort = table.getAttribute('data-sort-col');
    const currentDir = table.getAttribute('data-sort-dir') || 'asc';
    const newDir = (currentSort === String(colIndex) && currentDir === 'asc') ? 'desc' : 'asc';
    
    table.setAttribute('data-sort-col', colIndex);
    table.setAttribute('data-sort-dir', newDir);

    rows.sort((a, b) => {
        const cellA = a.children[colIndex] ? a.children[colIndex].innerText.trim() : '';
        const cellB = b.children[colIndex] ? b.children[colIndex].innerText.trim() : '';
        if (isNumeric) {
            const numA = parseFloat(cellA.replace(/[^0-9.-]+/g, '')) || 0;
            const numB = parseFloat(cellB.replace(/[^0-9.-]+/g, '')) || 0;
            return newDir === 'asc' ? numA - numB : numB - numA;
        }
        return newDir === 'asc' ? cellA.localeCompare(cellB) : cellB.localeCompare(cellA);
    });

    rows.forEach(r => tbody.appendChild(r));

    // Update sort indicators in th
    table.querySelectorAll('th .sort-icon').forEach(icon => icon.textContent = '↕');
    const activeHeader = table.querySelectorAll('th')[colIndex];
    if (activeHeader) {
        const icon = activeHeader.querySelector('.sort-icon');
        if (icon) icon.textContent = newDir === 'asc' ? '↑' : '↓';
    }
}

// Bulk row select
function toggleSelectAll(masterCheckbox, className = 'row-select') {
    const checkboxes = document.querySelectorAll('.' + className);
    checkboxes.forEach(cb => {
        cb.checked = masterCheckbox.checked;
    });
    updateBulkSelectionCount(className);
}

function updateBulkSelectionCount(className = 'row-select') {
    const selected = document.querySelectorAll('.' + className + ':checked').length;
    const countEl = document.getElementById('selected-count');
    const bulkBar = document.getElementById('bulk-actions-bar');
    if (countEl) countEl.textContent = selected;
    if (bulkBar) {
        if (selected > 0) {
            bulkBar.classList.remove('hidden');
        } else {
            bulkBar.classList.add('hidden');
        }
    }
}

// Confirm action
function confirmAction(message) {
    return confirm(message || 'Are you sure you want to proceed?');
}

// Slug generator
function generateSlug(text) {
    return text.toString().toLowerCase().trim()
        .replace(/\s+/g, '-')
        .replace(/[^\w\-]+/g, '')
        .replace(/\-\-+/g, '-');
}

// Character counter
function updateCharCount(inputId, countId, max) {
    const input = document.getElementById(inputId);
    const counter = document.getElementById(countId);
    if (input && counter) {
        const len = input.value.length;
        counter.textContent = `${len} / ${max}`;
        if (len > max) {
            counter.style.color = 'var(--danger)';
        } else {
            counter.style.color = 'var(--text-secondary)';
        }
    }
}

// Auto-dismiss alerts after 5 seconds
document.addEventListener('DOMContentLoaded', function() {
    setTimeout(function() {
        document.querySelectorAll('.alert-dismissible').forEach(function(alert) {
            alert.style.transition = 'opacity 0.3s ease, transform 0.3s ease';
            alert.style.opacity = '0';
            alert.style.transform = 'translateY(-10px)';
            setTimeout(() => alert.remove(), 300);
        });
    }, 5000);

    // Initial check for URL hash to activate tab
    if (window.location.hash) {
        const tab = window.location.hash.substring(1);
        if (document.getElementById('tab-' + tab)) {
            switchTab(tab);
        }
    }

    // Initialize admin language
    applyAdminLanguage(getAdminLang());
});

// Admin Panel Internationalization & Language Switcher
const ADMIN_I18N = {
    en: {
        nav_label_core: "Core Management",
        nav_dashboard: "Dashboard",
        nav_articles: "Articles",
        nav_categories: "Categories",
        nav_platforms: "Platforms",
        nav_label_community: "Community & Users",
        nav_users: "Users",
        nav_comments: "Comments",
        nav_label_system: "System & Logs",
        nav_activity: "Activity Logs",
        nav_notifications: "Notifications",
        nav_account: "Account Settings",
        nav_view_site: "View Public Site",
        nav_logout: "Log Out",
        search_placeholder: "Quick search...",
        breadcrumb_admin: "Admin",
        dd_profile: "My Profile",
        dd_security: "Change Password",
        dd_preferences: "Account Settings",
        dd_activity: "Activity Logs",
        dd_view_site: "View Website",
        dd_lang: "Switch to Hindi (हिंदी)",
        dd_theme: "Toggle Dark Mode",
        dd_logout: "Log Out",
        pref_lang_label: "Admin Interface Language"
    },
    hi: {
        nav_label_core: "मुख्य प्रबंधन",
        nav_dashboard: "डैशबोर्ड",
        nav_articles: "लेख",
        nav_categories: "श्रेणियाँ",
        nav_platforms: "प्लेटफ़ॉर्म",
        nav_label_community: "समुदाय एवं उपयोगकर्ता",
        nav_users: "उपयोगकर्ता",
        nav_comments: "टिप्पणियाँ",
        nav_label_system: "सिस्टम एवं लॉग्स",
        nav_activity: "गतिविधि लॉग्स",
        nav_notifications: "सूचनाएं",
        nav_account: "खाता सेटिंग्स",
        nav_view_site: "सार्वजनिक साइट देखें",
        nav_logout: "लॉग आउट",
        search_placeholder: "त्वरित खोजें...",
        breadcrumb_admin: "व्यवस्थापक",
        dd_profile: "मेरी प्रोफ़ाइल",
        dd_security: "पासवर्ड बदलें",
        dd_preferences: "खाता सेटिंग्स",
        dd_activity: "गतिविधि लॉग्स",
        dd_view_site: "वेबसाइट देखें",
        dd_lang: "अंग्रेज़ी में बदलें (EN)",
        dd_theme: "डार्क/लाइट मोड बदलें",
        dd_logout: "लॉग आउट",
        pref_lang_label: "प्रशासन इंटरफ़ेस भाषा"
    }
};

const ADMIN_TEXT_MAP_HI = {
    "Overview": "अवलोकन",
    "Notifications": "सूचनाएं",
    "View All": "सभी देखें",
    "All caught up! No unread notifications.": "कोई नई अपठित सूचना नहीं है।",
    "Review Notifications": "सूचनाएं देखें",
    "Admin": "व्यवस्थापक",
    "Administrator": "प्रशासक"
};

function getAdminLang() {
    try {
        const loc = localStorage.getItem('site_lang');
        if (loc === 'en' || loc === 'hi') return loc;
    } catch (e) {}
    const match = document.cookie.match(/(?:^|;\s*)site_lang=([^;]+)/);
    if (match && (match[1] === 'en' || match[1] === 'hi')) return match[1];
    return 'hi';
}

function applyAdminLanguage(lang) {
    if (lang !== 'en' && lang !== 'hi') lang = 'hi';
    const dict = ADMIN_I18N[lang] || ADMIN_I18N.hi;

    document.querySelectorAll('[data-admin-i18n]').forEach(el => {
        const key = el.getAttribute('data-admin-i18n');
        if (dict[key]) {
            el.textContent = dict[key];
        }
    });

    const badge = document.getElementById('admin-lang-badge');
    if (badge) {
        badge.textContent = lang.toUpperCase();
    }

    const searchInput = document.querySelector('.header-search input');
    if (searchInput && dict.search_placeholder) {
        searchInput.placeholder = dict.search_placeholder;
    }

    const cardHi = document.getElementById('admin-lang-card-hi');
    const cardEn = document.getElementById('admin-lang-card-en');
    if (cardHi && cardEn) {
        if (lang === 'hi') {
            cardHi.style.borderColor = 'var(--primary)';
            cardHi.style.background = 'var(--crimson-tint)';
            const checkHi = cardHi.querySelector('.lang-check');
            if (checkHi) checkHi.style.display = 'inline';

            cardEn.style.borderColor = 'var(--border)';
            cardEn.style.background = 'transparent';
            const checkEn = cardEn.querySelector('.lang-check');
            if (checkEn) checkEn.style.display = 'none';
        } else {
            cardEn.style.borderColor = 'var(--primary)';
            cardEn.style.background = 'var(--crimson-tint)';
            const checkEn = cardEn.querySelector('.lang-check');
            if (checkEn) checkEn.style.display = 'inline';

            cardHi.style.borderColor = 'var(--border)';
            cardHi.style.background = 'transparent';
            const checkHi = cardHi.querySelector('.lang-check');
            if (checkHi) checkHi.style.display = 'none';
        }
    }

    document.documentElement.lang = lang;
    translateAdminDynamicNodes(lang);
}

function setAdminLanguage(lang) {
    if (lang !== 'en' && lang !== 'hi') lang = 'hi';
    try { localStorage.setItem('site_lang', lang); } catch (e) {}
    document.cookie = `site_lang=${lang}; path=/; max-age=31536000; SameSite=Lax`;
    applyAdminLanguage(lang);
}

function toggleAdminLanguage() {
    const cur = getAdminLang();
    const next = (cur === 'en') ? 'hi' : 'en';
    setAdminLanguage(next);
}


const _adminTxCache = {};
async function fetchAdminTx(text, sl, tl) {
    const clean = text.trim();
    if (!clean) return text;
    const key = sl + "_" + tl + "_" + clean;
    if (_adminTxCache[key]) return _adminTxCache[key];
    try {
        const cached = sessionStorage.getItem("atx_" + key);
        if (cached) { _adminTxCache[key] = cached; return cached; }
    } catch (e) {}
    try {
        const resp = await fetch("https://translate.googleapis.com/translate_a/single?client=gtx&sl=" + sl + "&tl=" + tl + "&dt=t&q=" + encodeURIComponent(clean));
        if (!resp.ok) return text;
        const data = await resp.json();
        if (Array.isArray(data) && Array.isArray(data[0])) {
            const out = data[0].map(p => (p && p[0]) ? p[0] : "").join("");
            if (out) {
                _adminTxCache[key] = out;
                try { sessionStorage.setItem("atx_" + key, out); } catch (e) {}
                return out;
            }
        }
    } catch (e) {}
    return text;
}

let _adminTxVer = 0;
async function translateAdminDynamicNodes(lang) {
    const myVer = ++_adminTxVer;
    const isEn = (lang === "en");
    const root = document.querySelector(".main-wrapper") || document.body;
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, null, false);
    const nodes = [];
    let n;
    while ((n = walker.nextNode())) {
        const p = n.parentElement;
        if (!p || p.tagName === "SCRIPT" || p.tagName === "STYLE" || p.hasAttribute("data-admin-i18n") || p.id === "admin-lang-badge") continue;
        if (!n.nodeValue || !n.nodeValue.trim()) continue;
        if (n._origText === undefined) n._origText = n.nodeValue;
        nodes.push(n);
    }

    const targets = nodes.filter(node => {
        const orig = node._origText.trim();
        if (isEn) {
            return /[\u0900-\u097F]/.test(orig);
        } else {
            return /[A-Za-z]{3,}/.test(orig) && !/[\u0900-\u097F]/.test(orig) && !/^(https?:|@[a-zA-Z0-9_]+|[0-9\s\/:.,+-]+)$/.test(orig);
        }
    });

    // First restore originals before translating opposite direction
    nodes.forEach(node => {
        const orig = node._origText;
        // Strip bilingual parentheses if present
        const bilingualMatch = orig.match(/^(.+?)\s*\(([A-Za-z0-9\s,&/-]+)\)\s*$/);
        if (bilingualMatch && /[\u0900-\u097F]/.test(bilingualMatch[1])) {
            node.nodeValue = isEn ? bilingualMatch[2] : bilingualMatch[1];
            node._handledBilingual = true;
        } else {
            node._handledBilingual = false;
            node.nodeValue = orig;
        }
    });

    const remaining = targets.filter(node => !node._handledBilingual);
    const sl = isEn ? "hi" : "en";
    const tl = isEn ? "en" : "hi";
    for (let i = 0; i < remaining.length; i += 8) {
        if (myVer !== _adminTxVer) return;
        const batch = remaining.slice(i, i + 8);
        await Promise.all(batch.map(async node => {
            const orig = node._origText;
            const cacheProp = isEn ? "_enVal" : "_hiVal";
            if (node[cacheProp]) {
                if (myVer === _adminTxVer) node.nodeValue = node[cacheProp];
                return;
            }
            const lead = (orig.match(/^\s*/) || [""])[0];
            const trail = (orig.match(/\s*$/) || [""])[0];
            const tr = await fetchAdminTx(orig, sl, tl);
            if (tr && tr !== orig.trim()) {
                node[cacheProp] = lead + tr + trail;
                if (myVer === _adminTxVer) node.nodeValue = node[cacheProp];
            }
        }));
    }
}
