import os
import base64
import html as html_utils
import json
import re
import subprocess
import time
import uuid

import pypandoc
from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

try:
    import redis
except ImportError:
    redis = None

try:
    import pymysql
except ImportError:
    pymysql = None

app = FastAPI(title="Markdown Render API", version="1.0.0")

ALLOWED_FORMATS = {"html", "docx", "pdf"}
MEDIA_TYPES = {
    "html": "text/html",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}
SVG_RE = re.compile(r"<svg\b[\s\S]*?</svg>", re.IGNORECASE)
SVG_ATTR_RE = re.compile(r"""\b(width|height|viewBox)\s*=\s*(['"])(.*?)\2""", re.IGNORECASE)
FOREIGN_OBJECT_RE = re.compile(r"<foreignObject\b([^>]*)>([\s\S]*?)</foreignObject>", re.IGNORECASE)
SIMPLE_ATTR_RE = re.compile(r"""([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(['"])(.*?)\2""")
DATA_IMAGE_RE = re.compile(
    r"""(<img\b[^>]*\bsrc\s*=\s*['"])data:image/(png|jpe?g);base64,([^'"]+)(['"][^>]*>)""",
    re.IGNORECASE,
)
DOCX_IMAGE_SCALE_DEFAULT = 2
DOCX_IMAGE_SCALE_MIN = 1
DOCX_IMAGE_SCALE_MAX = 4
DOCX_MAX_IMAGE_WIDTH = 650
DOCX_MAX_RENDER_SIDE = 4096
DOCX_SVG_PADDING = 96
PDF_IMAGE_SCALE = 3
PDF_MAX_IMAGE_WIDTH = 1000
HISTORY_REDIS_PREFIX = os.getenv("HISTORY_REDIS_PREFIX", "md-workspace:history")
HISTORY_MYSQL_TABLE = os.getenv("HISTORY_MYSQL_TABLE", "markdown_history")
_MYSQL_HISTORY_TABLE_READY = False


@app.get("/", response_class=HTMLResponse)
async def get_gui():
    return r"""
    <!DOCTYPE html>
    <html lang="zh-CN">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>Markdown 工作站</title>
        <script src="https://cdn.tailwindcss.com"></script>
        <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"></script>
        <script src="https://cdn.jsdelivr.net/npm/mermaid/dist/mermaid.min.js"></script>
        <link rel="stylesheet" href="https://cdnjs.cloudflare.com/ajax/libs/github-markdown-css/5.5.0/github-markdown.min.css">
        <style>
            ::-webkit-scrollbar { width: 8px; height: 8px; }
            ::-webkit-scrollbar-thumb { background-color: #cbd5e1; border-radius: 4px; }
            .markdown-body { padding: 2rem; background-color: transparent !important; }
            .markdown-body .mermaid { text-align: center; margin: 20px 0; }
            .markdown-body svg { max-width: 100%; height: auto; }
            #editor:focus { outline: none; }
            .history-card.selected { border-color: #2563eb; background: #eff6ff; }
        </style>
    </head>
    <body class="bg-gray-50 h-screen flex flex-col overflow-hidden font-sans">
        <header class="bg-white shadow-sm h-16 flex items-center justify-between px-6 shrink-0 z-10">
            <div class="flex items-center gap-2">
                <span class="text-2xl">📝</span>
                <h1 class="text-lg font-bold text-gray-800">Markdown 工作站</h1>
                <button onclick="openHistoryModal()" class="ml-3 px-3 py-1.5 text-sm font-medium text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors">
                    历史记录
                </button>
            </div>
            <div class="flex items-center gap-3">
                <label class="flex items-center gap-2 text-sm text-gray-600">
                    <span>Word 图片</span>
                    <select id="docx-image-scale" class="h-9 rounded-lg border border-gray-200 bg-white px-2 text-sm text-gray-700">
                        <option value="1">标准 1x</option>
                        <option value="2" selected>清晰 2x</option>
                        <option value="3">高清 3x</option>
                        <option value="4">超清 4x</option>
                    </select>
                </label>
                <button onclick="exportDoc('html')" class="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-lg transition-colors">
                    ⬇️ 导出 HTML
                </button>
                <button onclick="exportDoc('docx')" class="px-4 py-2 text-sm font-medium text-blue-600 bg-blue-50 hover:bg-blue-100 rounded-lg transition-colors">
                    📘 导出 Word
                </button>
                <button onclick="exportDoc('pdf')" class="px-4 py-2 text-sm font-medium text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg shadow-sm transition-colors flex items-center gap-2">
                    <span id="pdf-spinner" class="hidden animate-spin h-4 w-4 border-2 border-white border-t-transparent rounded-full"></span>
                    📕 导出 PDF
                </button>
            </div>
        </header>

        <main class="flex-1 flex overflow-hidden">
            <div class="w-1/2 h-full border-r border-gray-200 bg-white flex flex-col">
                <div class="bg-gray-100/50 px-4 py-2 border-b border-gray-200 flex items-center justify-between gap-3">
                    <span class="text-xs font-semibold text-gray-500 uppercase tracking-wider">编辑区 (Markdown)</span>
                    <div class="flex items-center gap-2">
                        <input id="history-tags-input" class="h-8 w-56 rounded-lg border border-gray-200 bg-white px-3 text-sm text-gray-700" placeholder="标签，逗号分隔">
                        <button onclick="saveCurrentHistory()" class="h-8 px-3 text-sm font-medium text-white bg-emerald-600 hover:bg-emerald-700 rounded-lg transition-colors">
                            保存
                        </button>
                    </div>
                </div>
                <textarea id="editor" class="flex-1 w-full p-6 resize-none bg-transparent text-gray-800 font-mono text-sm leading-relaxed" placeholder="在此输入 Markdown 内容..."></textarea>
            </div>

            <div class="w-1/2 h-full bg-[#fdfdfd] flex flex-col overflow-hidden">
                <div class="bg-gray-100/50 px-4 py-2 border-b border-gray-200 text-xs font-semibold text-gray-500 uppercase tracking-wider">
                    实时预览
                </div>
                <div id="preview-container" class="flex-1 overflow-y-auto">
                    <div id="preview" class="markdown-body"></div>
                </div>
            </div>
        </main>

        <div id="history-modal" class="hidden fixed inset-0 z-50 bg-black/40">
            <div class="h-full w-full flex items-center justify-center p-6">
                <div class="w-full max-w-4xl max-h-[82vh] bg-white rounded-lg shadow-xl flex flex-col overflow-hidden">
                    <div class="px-5 py-4 border-b border-gray-200 flex items-center justify-between gap-4">
                        <div>
                            <div class="flex items-center gap-2">
                                <h2 class="text-lg font-semibold text-gray-900">历史记录</h2>
                                <div class="relative group">
                                    <button type="button" class="h-5 w-5 rounded-full border border-gray-300 text-xs font-semibold text-gray-500 hover:bg-gray-100" aria-label="历史记录策略说明">?</button>
                                    <div class="hidden group-hover:block absolute left-0 top-7 z-20 w-80 rounded-lg border border-gray-200 bg-white p-3 text-xs leading-5 text-gray-600 shadow-lg">
                                        <p class="font-medium text-gray-800">展示策略</p>
                                        <p class="mt-1">历史记录按更新时间倒序展示，最近保存或更新的记录排在最前。</p>
                                        <p class="mt-1">Redis：默认读取最近 200 条；搜索或标签过滤时扫描最近 1000 条，再返回匹配结果，最多 200 条。</p>
                                        <p class="mt-1">MySQL：在数据库侧按搜索条件过滤，再按更新时间倒序返回最多 200 条。</p>
                                        <p class="mt-1">保存记录本身不限制总量，需手动删除、清空或通过存储服务策略清理。</p>
                                    </div>
                                </div>
                            </div>
                            <p id="history-backend-status" class="text-xs text-gray-500 mt-1">正在检测同步状态...</p>
                        </div>
                        <button onclick="closeHistoryModal()" class="h-8 w-8 rounded-lg text-gray-500 hover:bg-gray-100 text-xl leading-none">×</button>
                    </div>
                    <div class="px-5 py-3 border-b border-gray-200 flex items-center gap-3">
                        <input id="history-search-input" class="h-9 flex-1 rounded-lg border border-gray-200 px-3 text-sm" placeholder="搜索标题、摘要、正文或标签">
                        <input id="history-tag-filter-input" class="h-9 w-64 rounded-lg border border-gray-200 px-3 text-sm" placeholder="过滤标签，逗号分隔">
                        <button onclick="loadHistoryRecords()" class="h-9 px-3 text-sm font-medium text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-lg">搜索</button>
                    </div>
                    <div id="history-list" class="flex-1 overflow-y-auto p-5 grid grid-cols-1 md:grid-cols-2 gap-3"></div>
                    <div class="px-5 py-4 border-t border-gray-200 flex items-center justify-between">
                        <button onclick="clearHistoryRecords()" class="px-3 py-2 text-sm font-medium text-red-600 bg-red-50 hover:bg-red-100 rounded-lg">清空历史</button>
                        <div class="flex items-center gap-3">
                            <button onclick="closeHistoryModal()" class="px-4 py-2 text-sm font-medium text-gray-700 bg-gray-100 hover:bg-gray-200 rounded-lg">取消</button>
                            <p id="history-selection-text" class="text-sm text-gray-500 max-w-64 truncate">请选择一条历史记录</p>
                            <button onclick="applySelectedHistory()" class="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg">确认覆盖</button>
                        </div>
                    </div>
                </div>
            </div>
        </div>

        <div id="toast-container" class="fixed top-5 left-1/2 -translate-x-1/2 z-[70] flex flex-col items-center gap-2 pointer-events-none"></div>

        <div id="confirm-modal" class="hidden fixed inset-0 z-[65] bg-gray-900/35">
            <div class="h-full w-full flex items-start justify-center px-4 pt-24">
                <div class="w-full max-w-md rounded-lg bg-white shadow-xl border border-gray-200 overflow-hidden">
                    <div class="px-5 py-4 border-b border-gray-100">
                        <h2 id="confirm-title" class="text-base font-semibold text-gray-900">请确认</h2>
                        <p id="confirm-message" class="mt-2 text-sm leading-6 text-gray-600"></p>
                    </div>
                    <div class="px-5 py-4 bg-gray-50 flex justify-end gap-2">
                        <button id="confirm-cancel" class="px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-200 hover:bg-gray-100 rounded-lg">取消</button>
                        <button id="confirm-ok" class="px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg">确认</button>
                    </div>
                </div>
            </div>
        </div>

        <script>
            const editor = document.getElementById('editor');
            const preview = document.getElementById('preview');
            const previewContainer = document.getElementById('preview-container');
            const docxImageScale = document.getElementById('docx-image-scale');
            const historyTagsInput = document.getElementById('history-tags-input');
            const historyModal = document.getElementById('history-modal');
            const historyList = document.getElementById('history-list');
            const historySearchInput = document.getElementById('history-search-input');
            const historyTagFilterInput = document.getElementById('history-tag-filter-input');
            const historySelectionText = document.getElementById('history-selection-text');
            const historyBackendStatus = document.getElementById('history-backend-status');
            const toastContainer = document.getElementById('toast-container');
            const confirmModal = document.getElementById('confirm-modal');
            const confirmTitle = document.getElementById('confirm-title');
            const confirmMessage = document.getElementById('confirm-message');
            const confirmCancel = document.getElementById('confirm-cancel');
            const confirmOk = document.getElementById('confirm-ok');
            const historyStorageKey = 'md-workspace-history-v1';
            let historyBackend = { enabled: false, backend: 'local' };
            let selectedHistoryId = null;
            let currentHistoryItems = [];
            let activeConfirmResolver = null;

            mermaid.initialize({ startOnLoad: false, theme: 'default', securityLevel: 'loose' });
            marked.setOptions({ breaks: true, gfm: true });

            function showToast(message, type = 'info') {
                const colorMap = {
                    success: 'border-emerald-200 bg-emerald-50 text-emerald-800',
                    error: 'border-red-200 bg-red-50 text-red-800',
                    warning: 'border-amber-200 bg-amber-50 text-amber-800',
                    info: 'border-blue-200 bg-blue-50 text-blue-800'
                };
                const toast = document.createElement('div');
                toast.className = `pointer-events-auto min-w-72 max-w-[520px] rounded-lg border px-4 py-3 text-sm shadow-lg ${colorMap[type] || colorMap.info}`;
                toast.textContent = message;
                toastContainer.appendChild(toast);
                window.setTimeout(() => {
                    toast.classList.add('opacity-0', '-translate-y-2', 'transition-all', 'duration-200');
                    window.setTimeout(() => toast.remove(), 220);
                }, 2400);
            }

            function resolveConfirm(value) {
                confirmModal.classList.add('hidden');
                if (activeConfirmResolver) {
                    activeConfirmResolver(value);
                    activeConfirmResolver = null;
                }
            }

            function showConfirm(message, options = {}) {
                confirmTitle.textContent = options.title || '请确认';
                confirmMessage.textContent = message;
                confirmOk.textContent = options.okText || '确认';
                confirmCancel.textContent = options.cancelText || '取消';
                confirmOk.className = options.danger
                    ? 'px-4 py-2 text-sm font-medium text-white bg-red-600 hover:bg-red-700 rounded-lg'
                    : 'px-4 py-2 text-sm font-medium text-white bg-blue-600 hover:bg-blue-700 rounded-lg';
                confirmModal.classList.remove('hidden');
                confirmOk.focus();
                return new Promise((resolve) => {
                    activeConfirmResolver = resolve;
                });
            }

            function parseTags(value) {
                return String(value || '')
                    .split(/[,，\s]+/)
                    .map((tag) => tag.trim())
                    .filter(Boolean)
                    .slice(0, 12);
            }

            function loadLocalHistory() {
                try {
                    return JSON.parse(localStorage.getItem(historyStorageKey) || '[]');
                } catch (error) {
                    return [];
                }
            }

            function saveLocalHistoryItem(item) {
                const items = loadLocalHistory().filter((existing) => existing.id !== item.id);
                items.unshift(item);
                localStorage.setItem(historyStorageKey, JSON.stringify(items.slice(0, 200)));
            }

            function deleteLocalHistoryItem(itemId) {
                const items = loadLocalHistory().filter((existing) => existing.id !== itemId);
                localStorage.setItem(historyStorageKey, JSON.stringify(items));
            }

            function clearLocalHistory() {
                localStorage.removeItem(historyStorageKey);
            }

            function summarizeContent(content) {
                const lines = content.split('\n').map((line) => line.trim()).filter(Boolean);
                const firstLine = lines[0] || '未命名记录';
                const title = firstLine.replace(/^#+\s*/, '').slice(0, 80) || '未命名记录';
                const summary = content
                    .replace(/```[\s\S]*?```/g, ' ')
                    .replace(/[#>*_`\[\]()]|!\[[^\]]*\]/g, ' ')
                    .replace(/\s+/g, ' ')
                    .trim()
                    .slice(0, 220);
                return { title, summary };
            }

            function buildHistoryItem() {
                const content = editor.value;
                const { title, summary } = summarizeContent(content);
                const now = Date.now();
                return {
                    id: `${now}-${Math.random().toString(16).slice(2)}`,
                    title,
                    summary,
                    content,
                    tags: parseTags(historyTagsInput.value),
                    createdAt: now,
                    updatedAt: now
                };
            }

            async function detectHistoryBackend() {
                try {
                    const response = await fetch('/api/history/config');
                    historyBackend = await response.json();
                } catch (error) {
                    historyBackend = { enabled: false, backend: 'local', message: '使用浏览器本地历史' };
                }
                historyBackendStatus.textContent = historyBackend.enabled
                    ? `同步后端：${historyBackend.backend}`
                    : '未配置 Redis/MySQL，使用浏览器本地历史';
            }

            function matchesLocalHistory(item, query, tags) {
                const haystack = `${item.title || ''} ${item.summary || ''} ${item.content || ''} ${(item.tags || []).join(' ')}`.toLowerCase();
                if (query && !haystack.includes(query.toLowerCase())) return false;
                const itemTags = new Set(item.tags || []);
                return tags.every((tag) => itemTags.has(tag));
            }

            async function saveCurrentHistory() {
                if (!editor.value.trim()) {
                    showToast('内容为空，无法保存', 'warning');
                    return;
                }

                const item = buildHistoryItem();
                saveLocalHistoryItem(item);

                if (historyBackend.enabled) {
                    try {
                        const response = await fetch('/api/history', {
                            method: 'POST',
                            headers: { 'Content-Type': 'application/json' },
                            body: JSON.stringify(item)
                        });
                        if (!response.ok) throw new Error('远端保存失败');
                        const result = await response.json();
                        if (result.item) saveLocalHistoryItem(result.item);
                    } catch (error) {
                        console.warn('远端历史保存失败，已保存在浏览器本地:', error.message);
                    }
                }

                showToast('已保存到历史记录', 'success');
            }

            async function deleteHistoryRecord(itemId) {
                const selected = currentHistoryItems.find((item) => item.id === itemId);
                const title = selected ? selected.title : '该历史记录';
                const confirmed = await showConfirm(`确认删除“${title}”？`, {
                    title: '删除历史记录',
                    okText: '删除',
                    danger: true
                });
                if (!confirmed) return;

                deleteLocalHistoryItem(itemId);
                currentHistoryItems = currentHistoryItems.filter((item) => item.id !== itemId);
                if (selectedHistoryId === itemId) selectedHistoryId = null;
                renderHistoryList();
                showToast('历史记录已删除', 'success');

                if (historyBackend.enabled) {
                    try {
                        const response = await fetch(`/api/history/${encodeURIComponent(itemId)}`, { method: 'DELETE' });
                        if (!response.ok) throw new Error('远端删除失败');
                    } catch (error) {
                        console.warn('远端历史删除失败，本地记录已删除:', error.message);
                    }
                }
            }

            async function clearHistoryRecords() {
                const confirmed = await showConfirm('确认清空所有历史记录？该操作不可恢复。', {
                    title: '清空历史记录',
                    okText: '清空',
                    danger: true
                });
                if (!confirmed) return;

                clearLocalHistory();
                currentHistoryItems = [];
                selectedHistoryId = null;
                renderHistoryList();
                showToast('历史记录已清空', 'success');

                if (historyBackend.enabled) {
                    try {
                        const response = await fetch('/api/history', { method: 'DELETE' });
                        if (!response.ok) throw new Error('远端清空失败');
                    } catch (error) {
                        console.warn('远端历史清空失败，本地记录已清空:', error.message);
                    }
                }
            }

            async function loadHistoryRecords() {
                const query = historySearchInput.value.trim();
                const tags = parseTags(historyTagFilterInput.value);
                let items = [];

                if (historyBackend.enabled) {
                    try {
                        const params = new URLSearchParams({ q: query, tags: tags.join(',') });
                        const response = await fetch(`/api/history?${params.toString()}`);
                        if (!response.ok) throw new Error('远端历史读取失败');
                        const result = await response.json();
                        items = result.items || [];
                    } catch (error) {
                        console.warn('远端历史读取失败，使用浏览器本地历史:', error.message);
                    }
                }

                if (!items.length) {
                    items = loadLocalHistory().filter((item) => matchesLocalHistory(item, query, tags));
                }

                currentHistoryItems = items.sort((a, b) => (b.updatedAt || 0) - (a.updatedAt || 0));
                selectedHistoryId = null;
                renderHistoryList();
            }

            function formatHistoryTime(value) {
                if (!value) return '';
                return new Date(value).toLocaleString();
            }

            function renderHistoryList() {
                historySelectionText.textContent = '请选择一条历史记录';
                if (!currentHistoryItems.length) {
                    historyList.innerHTML = '<div class="col-span-full text-sm text-gray-500 py-10 text-center">没有匹配的历史记录</div>';
                    return;
                }

                historyList.innerHTML = currentHistoryItems.map((item) => `
                    <div role="button" tabindex="0" data-history-id="${escapeHtml(item.id)}" class="history-card text-left border border-gray-200 rounded-lg p-4 hover:border-blue-300 transition-colors cursor-pointer">
                        <div class="flex items-start justify-between gap-3">
                            <h3 class="font-semibold text-gray-900 line-clamp-1">${escapeHtml(item.title || '未命名记录')}</h3>
                            <div class="flex items-center gap-2 shrink-0">
                                <span class="text-xs text-gray-400">${escapeHtml(formatHistoryTime(item.updatedAt))}</span>
                                <button type="button" data-delete-history-id="${escapeHtml(item.id)}" class="px-2 py-1 text-xs font-medium text-red-600 bg-red-50 hover:bg-red-100 rounded">删除</button>
                            </div>
                        </div>
                        <p class="mt-2 text-sm text-gray-600 line-clamp-3">${escapeHtml(item.summary || '')}</p>
                        <div class="mt-3 flex flex-wrap gap-1">
                            ${(item.tags || []).map((tag) => `<span class="px-2 py-0.5 rounded bg-gray-100 text-xs text-gray-600">${escapeHtml(tag)}</span>`).join('')}
                        </div>
                    </div>
                `).join('');
            }

            function escapeHtml(value) {
                return String(value || '')
                    .replace(/&/g, '&amp;')
                    .replace(/</g, '&lt;')
                    .replace(/>/g, '&gt;')
                    .replace(/"/g, '&quot;')
                    .replace(/'/g, '&#39;');
            }

            async function openHistoryModal() {
                historyModal.classList.remove('hidden');
                await detectHistoryBackend();
                await loadHistoryRecords();
            }

            function closeHistoryModal() {
                historyModal.classList.add('hidden');
            }

            async function applySelectedHistory() {
                const selected = currentHistoryItems.find((item) => item.id === selectedHistoryId);
                if (!selected) {
                    showToast('请先选择一条历史记录', 'warning');
                    return;
                }
                const confirmed = await showConfirm('确认用该历史记录覆盖当前编辑区内容？', {
                    title: '覆盖当前内容',
                    okText: '确认覆盖'
                });
                if (!confirmed) return;
                editor.value = selected.content || '';
                historyTagsInput.value = (selected.tags || []).join(', ');
                closeHistoryModal();
                await render();
                showToast('已覆盖当前编辑区内容', 'success');
            }

            historySearchInput.addEventListener('input', () => {
                clearTimeout(historySearchInput._timer);
                historySearchInput._timer = setTimeout(loadHistoryRecords, 250);
            });
            historyTagFilterInput.addEventListener('input', () => {
                clearTimeout(historyTagFilterInput._timer);
                historyTagFilterInput._timer = setTimeout(loadHistoryRecords, 250);
            });
            historyModal.addEventListener('click', (event) => {
                if (event.target === historyModal) closeHistoryModal();
            });
            confirmCancel.addEventListener('click', () => resolveConfirm(false));
            confirmOk.addEventListener('click', () => resolveConfirm(true));
            confirmModal.addEventListener('click', (event) => {
                if (event.target === confirmModal) resolveConfirm(false);
            });
            historyList.addEventListener('click', (event) => {
                const deleteButton = event.target.closest('[data-delete-history-id]');
                if (deleteButton) {
                    event.stopPropagation();
                    deleteHistoryRecord(deleteButton.dataset.deleteHistoryId);
                    return;
                }

                const card = event.target.closest('[data-history-id]');
                if (!card) return;
                selectedHistoryId = card.dataset.historyId;
                historyList.querySelectorAll('.history-card').forEach((node) => node.classList.remove('selected'));
                card.classList.add('selected');
                const selected = currentHistoryItems.find((item) => item.id === selectedHistoryId);
                historySelectionText.textContent = selected ? `已选择：${selected.title}` : '请选择一条历史记录';
            });
            document.addEventListener('keydown', (event) => {
                if (event.key === 'Escape' && !confirmModal.classList.contains('hidden')) {
                    resolveConfirm(false);
                    return;
                }
                if (event.key === 'Escape' && !historyModal.classList.contains('hidden')) {
                    closeHistoryModal();
                }
            });

            function replaceMermaidBlocks(container) {
                container.querySelectorAll('pre > code.language-mermaid').forEach((block) => {
                    const div = document.createElement('div');
                    div.className = 'mermaid';
                    div.textContent = block.textContent;
                    block.parentElement.replaceWith(div);
                });
            }

            async function render() {
                preview.innerHTML = marked.parse(editor.value);
                replaceMermaidBlocks(preview);

                try {
                    await mermaid.run({ nodes: preview.querySelectorAll('.mermaid') });
                } catch (error) {
                    console.warn('Mermaid 渲染失败:', error.message);
                }
            }

            function buildExportDocument(bodyHtml) {
                return `<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<style>
body {
    font-family: "WenQuanYi Micro Hei", "WenQuanYi Zen Hei", Arial, sans-serif;
    line-height: 1.65;
    color: #24292f;
    padding: 32px;
}
h1, h2, h3 { line-height: 1.25; }
pre {
    background: #f6f8fa;
    border-radius: 6px;
    padding: 16px;
    overflow: auto;
}
code { font-family: Consolas, "Liberation Mono", monospace; }
table {
    border-collapse: collapse;
    width: 100%;
    margin: 16px 0;
}
th, td {
    border: 1px solid #d0d7de;
    padding: 6px 13px;
}
th { background: #f6f8fa; }
blockquote {
    border-left: 4px solid #d0d7de;
    color: #57606a;
    margin: 16px 0;
    padding: 0 16px;
}
.mermaid, .diagram {
    text-align: center;
    margin: 20px 0;
}
svg, img {
    max-width: 100%;
    height: auto;
}
</style>
</head>
<body>
<article class="markdown-body">
${bodyHtml}
</article>
</body>
</html>`;
            }

            function svgDimensions(svgText) {
                const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml');
                const svg = doc.documentElement;

                function parseLength(value) {
                    if (!value || value.includes('%')) return null;
                    const match = value.match(/^\s*([0-9.]+)/);
                    return match ? Number(match[1]) : null;
                }

                let width = parseLength(svg.getAttribute('width'));
                let height = parseLength(svg.getAttribute('height'));
                const viewBox = svg.getAttribute('viewBox');
                if ((!width || !height) && viewBox) {
                    const parts = viewBox.replace(/,/g, ' ').trim().split(/\s+/).map(Number);
                    if (parts.length === 4 && parts.every(Number.isFinite)) {
                        width = width || parts[2];
                        height = height || parts[3];
                    }
                }

                return {
                    width: Math.max(Math.round(width || 900), 100),
                    height: Math.max(Math.round(height || 500), 100)
                };
            }

            function docxDisplaySize(width, height) {
                const maxWidth = 650;
                if (width <= maxWidth) return { width, height };
                const ratio = maxWidth / width;
                return { width: maxWidth, height: Math.max(Math.round(height * ratio), 1) };
            }

            function normalizeSvgForPng(svgText, width, height) {
                const doc = new DOMParser().parseFromString(svgText, 'image/svg+xml');
                const svg = doc.documentElement;
                svg.setAttribute('xmlns', 'http://www.w3.org/2000/svg');
                svg.setAttribute('width', String(width));
                svg.setAttribute('height', String(height));
                svg.setAttribute('style', `${svg.getAttribute('style') || ''};max-width:none;width:${width}px;height:${height}px;`);
                return new XMLSerializer().serializeToString(svg);
            }

            async function svgToPngDataUrl(svgText, imageScale) {
                const { width, height } = svgDimensions(svgText);
                const scale = Math.min(Math.max(Number(imageScale) || 2, 1), 4);
                const maxSide = 4096;
                let renderWidth = width * scale;
                let renderHeight = height * scale;
                if (renderWidth > maxSide || renderHeight > maxSide) {
                    const ratio = Math.min(maxSide / renderWidth, maxSide / renderHeight);
                    renderWidth = Math.max(Math.round(renderWidth * ratio), 1);
                    renderHeight = Math.max(Math.round(renderHeight * ratio), 1);
                }

                const normalizedSvg = normalizeSvgForPng(svgText, renderWidth, renderHeight);
                const svgBlob = new Blob([normalizedSvg], { type: 'image/svg+xml;charset=utf-8' });
                const url = URL.createObjectURL(svgBlob);

                try {
                    const image = new Image();
                    image.decoding = 'async';
                    const loaded = new Promise((resolve, reject) => {
                        image.onload = resolve;
                        image.onerror = reject;
                    });
                    image.src = url;
                    await loaded;

                    const canvas = document.createElement('canvas');
                    canvas.width = renderWidth;
                    canvas.height = renderHeight;
                    const context = canvas.getContext('2d');
                    context.fillStyle = '#ffffff';
                    context.fillRect(0, 0, renderWidth, renderHeight);
                    context.drawImage(image, 0, 0, renderWidth, renderHeight);

                    return {
                        dataUrl: canvas.toDataURL('image/png'),
                        ...docxDisplaySize(width, height)
                    };
                } finally {
                    URL.revokeObjectURL(url);
                }
            }

            async function markdownToExportHtml(markdownText, format, imageScale) {
                const container = document.createElement('div');
                container.innerHTML = marked.parse(markdownText);

                const blocks = Array.from(container.querySelectorAll('pre > code.language-mermaid'));
                for (let index = 0; index < blocks.length; index += 1) {
                    const block = blocks[index];
                    const id = `mermaid-export-${Date.now()}-${index}`;
                    const diagram = document.createElement('div');
                    diagram.className = 'diagram';

                    try {
                        const result = await mermaid.render(id, block.textContent);
                        diagram.innerHTML = result.svg;
                    } catch (error) {
                        diagram.textContent = `Mermaid 渲染失败: ${error.message}`;
                    }

                    block.parentElement.replaceWith(diagram);
                }

                return buildExportDocument(container.innerHTML);
            }

            let timeout;
            editor.addEventListener('input', () => {
                clearTimeout(timeout);
                timeout = setTimeout(render, 300);
            });

            editor.addEventListener('scroll', () => {
                const maxEditorScroll = editor.scrollHeight - editor.clientHeight;
                const maxPreviewScroll = previewContainer.scrollHeight - previewContainer.clientHeight;
                const percentage = maxEditorScroll > 0 ? editor.scrollTop / maxEditorScroll : 0;
                previewContainer.scrollTop = percentage * maxPreviewScroll;
            });

            async function exportDoc(format) {
                const markdownText = editor.value;
                if (!markdownText.trim()) {
                    showToast('内容为空，无法导出', 'warning');
                    return;
                }

                const btnSpinner = document.getElementById('pdf-spinner');
                if (format === 'pdf') btnSpinner.classList.remove('hidden');

                try {
                    const imageScale = Number(docxImageScale.value || '2');
                    const htmlText = await markdownToExportHtml(markdownText, format, imageScale);
                    const formData = new FormData();
                    formData.append('html_text', htmlText);
                    formData.append('format', format);
                    if (format === 'docx') {
                        formData.append('docx_image_scale', docxImageScale.value);
                    }

                    const response = await fetch('/api/render', { method: 'POST', body: formData });
                    if (!response.ok) {
                        const errorBody = await response.json().catch(() => ({}));
                        throw new Error(errorBody.detail || '导出失败');
                    }

                    const blob = await response.blob();
                    const url = window.URL.createObjectURL(blob);
                    const a = document.createElement('a');
                    a.style.display = 'none';
                    a.href = url;
                    a.download = `document.${format}`;
                    document.body.appendChild(a);
                    a.click();
                    a.remove();
                    window.URL.revokeObjectURL(url);
                } catch (error) {
                    showToast(error.message, 'error');
                } finally {
                    if (format === 'pdf') btnSpinner.classList.add('hidden');
                }
            }

            editor.value = `# Mermaid 导出测试

右侧可以实时预览 Mermaid 图表，右上角可以导出 HTML、Word、PDF。

\`\`\`mermaid
erDiagram
    CUSTOMER ||--o{ ORDER : places
    ORDER ||--|{ LINE_ITEM : contains
    CUSTOMER }|..|{ DELIVERY_ADDRESS : uses
\`\`\`

## 表格测试

| 字段 | 类型 | 说明 |
|---|---|---|
| id | int | 主键 |
| name | string | 姓名 |
`;
            render();
            detectHistoryBackend();
        </script>
    </body>
    </html>
    """


def cleanup_files(files: list[str]):
    for file_path in files:
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass


def history_backend_name() -> str | None:
    backend = os.getenv("HISTORY_BACKEND", "").strip().lower()
    if backend in {"redis", "mysql"}:
        return backend
    if backend and backend not in {"auto", "local", "none"}:
        return None
    if os.getenv("REDIS_URL") or os.getenv("REDIS_HOST"):
        return "redis"
    if os.getenv("MYSQL_HOST"):
        return "mysql"
    return None


def history_config() -> dict:
    backend = history_backend_name()
    if backend == "redis":
        return {
            "enabled": redis is not None,
            "backend": "redis" if redis is not None else "local",
            "configuredBackend": "redis",
            "message": "redis client unavailable" if redis is None else "redis history enabled",
        }
    if backend == "mysql":
        return {
            "enabled": pymysql is not None,
            "backend": "mysql" if pymysql is not None else "local",
            "configuredBackend": "mysql",
            "message": "pymysql client unavailable" if pymysql is None else "mysql history enabled",
        }
    return {
        "enabled": False,
        "backend": "local",
        "configuredBackend": None,
        "message": "remote history is not configured",
    }


def redis_client():
    if redis is None:
        raise RuntimeError("redis client unavailable")
    redis_url = os.getenv("REDIS_URL")
    if redis_url:
        return redis.Redis.from_url(redis_url, decode_responses=True)
    return redis.Redis(
        host=os.getenv("REDIS_HOST", "localhost"),
        port=int(os.getenv("REDIS_PORT", "6379")),
        db=int(os.getenv("REDIS_DB", "0")),
        username=os.getenv("REDIS_USERNAME") or None,
        password=os.getenv("REDIS_PASSWORD") or None,
        decode_responses=True,
    )


def mysql_connection():
    if pymysql is None:
        raise RuntimeError("pymysql client unavailable")
    return pymysql.connect(
        host=os.getenv("MYSQL_HOST", "localhost"),
        port=int(os.getenv("MYSQL_PORT", "3306")),
        user=os.getenv("MYSQL_USER", "root"),
        password=os.getenv("MYSQL_PASSWORD", ""),
        database=os.getenv("MYSQL_DATABASE", "markdown_workspace"),
        charset="utf8mb4",
        autocommit=True,
        cursorclass=pymysql.cursors.DictCursor,
    )


def ensure_mysql_history_table(conn):
    global _MYSQL_HISTORY_TABLE_READY
    if _MYSQL_HISTORY_TABLE_READY:
        return

    with conn.cursor() as cursor:
        cursor.execute(
            f"""
            CREATE TABLE IF NOT EXISTS `{HISTORY_MYSQL_TABLE}` (
                `id` VARCHAR(64) PRIMARY KEY,
                `title` VARCHAR(255) NOT NULL,
                `summary` TEXT NOT NULL,
                `content` LONGTEXT NOT NULL,
                `tags` TEXT NOT NULL,
                `created_at` BIGINT NOT NULL,
                `updated_at` BIGINT NOT NULL,
                INDEX `idx_updated_at` (`updated_at`),
                INDEX `idx_title` (`title`)
            ) CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci
            """
        )
        for index_sql in (
            f"ALTER TABLE `{HISTORY_MYSQL_TABLE}` ADD INDEX `idx_updated_at` (`updated_at`)",
            f"ALTER TABLE `{HISTORY_MYSQL_TABLE}` ADD INDEX `idx_title` (`title`)",
        ):
            try:
                cursor.execute(index_sql)
            except Exception as exc:
                error_code = exc.args[0] if getattr(exc, "args", None) else None
                if error_code != 1061 and "Duplicate key name" not in str(exc):
                    raise
    _MYSQL_HISTORY_TABLE_READY = True


def normalize_tags(tags) -> list[str]:
    if isinstance(tags, str):
        raw_tags = re.split(r"[,，\s]+", tags)
    elif isinstance(tags, list):
        raw_tags = tags
    else:
        raw_tags = []
    normalized = []
    seen = set()
    for tag in raw_tags:
        clean = str(tag).strip()
        if clean and clean not in seen:
            normalized.append(clean)
            seen.add(clean)
    return normalized[:12]


def summarize_markdown(content: str) -> tuple[str, str]:
    lines = [line.strip() for line in content.splitlines() if line.strip()]
    title = "未命名记录"
    for line in lines:
        title = re.sub(r"^#+\s*", "", line).strip() or title
        if title:
            break
    plain = re.sub(r"`{3}[\s\S]*?`{3}", " ", content)
    plain = re.sub(r"[#>*_`\[\]()]|!\[[^\]]*\]", " ", plain)
    plain = " ".join(plain.split())
    return title[:80], plain[:220]


def build_history_item(payload: dict, existing: dict | None = None) -> dict:
    content = str(payload.get("content") or "")
    if not content.strip():
        raise ValueError("content is required")
    now = int(time.time() * 1000)
    title, summary = summarize_markdown(content)
    item_id = str(payload.get("id") or (existing or {}).get("id") or uuid.uuid4())
    return {
        "id": item_id,
        "title": str(payload.get("title") or title),
        "summary": summary,
        "content": content,
        "tags": normalize_tags(payload.get("tags")),
        "createdAt": int((existing or {}).get("createdAt") or now),
        "updatedAt": now,
    }


def history_matches(item: dict, query: str, tags: list[str]) -> bool:
    haystack = " ".join([
        item.get("title", ""),
        item.get("summary", ""),
        item.get("content", ""),
        " ".join(item.get("tags", [])),
    ]).lower()
    if query and query.lower() not in haystack:
        return False
    item_tags = set(item.get("tags", []))
    return all(tag in item_tags for tag in tags)


def redis_history_save(item: dict) -> dict:
    client = redis_client()
    key = f"{HISTORY_REDIS_PREFIX}:item:{item['id']}"
    pipe = client.pipeline(transaction=False)
    pipe.set(key, json.dumps(item, ensure_ascii=False))
    pipe.zadd(f"{HISTORY_REDIS_PREFIX}:index", {item["id"]: item["updatedAt"]})
    pipe.execute()
    return item


def redis_history_list(query: str, tags: list[str]) -> list[dict]:
    client = redis_client()
    scan_count = 1000 if query or tags else 200
    ids = client.zrevrange(f"{HISTORY_REDIS_PREFIX}:index", 0, scan_count - 1)
    if not ids:
        return []

    keys = [f"{HISTORY_REDIS_PREFIX}:item:{item_id}" for item_id in ids]
    raw_items = client.mget(keys)
    items = []
    stale_ids = []
    for item_id, raw in zip(ids, raw_items):
        if not raw:
            stale_ids.append(item_id)
            continue
        try:
            item = json.loads(raw)
        except json.JSONDecodeError:
            stale_ids.append(item_id)
            continue
        if history_matches(item, query, tags):
            items.append(item)
            if len(items) >= 200:
                break
    if stale_ids:
        client.zrem(f"{HISTORY_REDIS_PREFIX}:index", *stale_ids)
    return items


def redis_history_delete(item_id: str) -> bool:
    client = redis_client()
    pipe = client.pipeline(transaction=False)
    pipe.delete(f"{HISTORY_REDIS_PREFIX}:item:{item_id}")
    pipe.zrem(f"{HISTORY_REDIS_PREFIX}:index", item_id)
    deleted, removed = pipe.execute()
    return bool(deleted or removed)


def redis_history_clear() -> int:
    client = redis_client()
    keys = list(client.scan_iter(match=f"{HISTORY_REDIS_PREFIX}:item:*", count=500))
    total = len(keys)
    pipe = client.pipeline(transaction=False)
    if keys:
        pipe.delete(*keys)
    pipe.delete(f"{HISTORY_REDIS_PREFIX}:index")
    pipe.execute()
    return total


def mysql_history_save(item: dict) -> dict:
    with mysql_connection() as conn:
        ensure_mysql_history_table(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                INSERT INTO `{HISTORY_MYSQL_TABLE}`
                    (`id`, `title`, `summary`, `content`, `tags`, `created_at`, `updated_at`)
                VALUES (%s, %s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    `title` = VALUES(`title`),
                    `summary` = VALUES(`summary`),
                    `content` = VALUES(`content`),
                    `tags` = VALUES(`tags`),
                    `updated_at` = VALUES(`updated_at`)
                """,
                (
                    item["id"],
                    item["title"],
                    item["summary"],
                    item["content"],
                    json.dumps(item["tags"], ensure_ascii=False),
                    item["createdAt"],
                    item["updatedAt"],
                ),
            )
    return item


def mysql_history_list(query: str, tags: list[str]) -> list[dict]:
    conditions = []
    params = []
    if query:
        like_query = f"%{query}%"
        conditions.append(
            "(`title` LIKE %s OR `summary` LIKE %s OR `content` LIKE %s OR `tags` LIKE %s)"
        )
        params.extend([like_query, like_query, like_query, like_query])
    for tag in tags:
        conditions.append("`tags` LIKE %s")
        params.append(f"%{json.dumps(tag, ensure_ascii=False)}%")
    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    with mysql_connection() as conn:
        ensure_mysql_history_table(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT `id`, `title`, `summary`, `content`, `tags`, `created_at`, `updated_at`
                FROM `{HISTORY_MYSQL_TABLE}`
                {where_sql}
                ORDER BY `updated_at` DESC
                LIMIT 200
                """,
                params,
            )
            rows = cursor.fetchall()
    items = []
    for row in rows:
        try:
            row_tags = json.loads(row["tags"] or "[]")
        except json.JSONDecodeError:
            row_tags = []
        item = {
            "id": row["id"],
            "title": row["title"],
            "summary": row["summary"],
            "content": row["content"],
            "tags": row_tags,
            "createdAt": int(row["created_at"]),
            "updatedAt": int(row["updated_at"]),
        }
        items.append(item)
    return items


def mysql_history_delete(item_id: str) -> bool:
    with mysql_connection() as conn:
        ensure_mysql_history_table(conn)
        with conn.cursor() as cursor:
            cursor.execute(
                f"DELETE FROM `{HISTORY_MYSQL_TABLE}` WHERE `id` = %s",
                (item_id,),
            )
            return cursor.rowcount > 0


def mysql_history_clear() -> int:
    with mysql_connection() as conn:
        ensure_mysql_history_table(conn)
        with conn.cursor() as cursor:
            cursor.execute(f"DELETE FROM `{HISTORY_MYSQL_TABLE}`")
            return cursor.rowcount


@app.get("/api/history/config")
def get_history_config():
    return history_config()


@app.get("/api/history")
def list_history(q: str = "", tags: str = ""):
    config = history_config()
    if not config["enabled"]:
        return {"backend": "local", "items": []}
    try:
        filter_tags = normalize_tags(tags)
        if config["backend"] == "redis":
            items = redis_history_list(q.strip(), filter_tags)
        elif config["backend"] == "mysql":
            items = mysql_history_list(q.strip(), filter_tags)
        else:
            items = []
        return {"backend": config["backend"], "items": items}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"backend": "local", "items": [], "detail": str(exc)},
        )


@app.post("/api/history")
async def save_history(request: Request):
    config = history_config()
    if not config["enabled"]:
        return JSONResponse(
            status_code=503,
            content={"backend": "local", "detail": "remote history is not configured"},
        )
    payload = await request.json()
    try:
        item = build_history_item(payload)
        if config["backend"] == "redis":
            saved = redis_history_save(item)
        elif config["backend"] == "mysql":
            saved = mysql_history_save(item)
        else:
            raise RuntimeError("remote history is not configured")
        return {"backend": config["backend"], "item": saved}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"backend": "local", "detail": str(exc)},
        )


@app.delete("/api/history/{item_id}")
def delete_history(item_id: str):
    config = history_config()
    if not config["enabled"]:
        return JSONResponse(
            status_code=503,
            content={"backend": "local", "detail": "remote history is not configured"},
        )
    try:
        if config["backend"] == "redis":
            deleted = redis_history_delete(item_id)
        elif config["backend"] == "mysql":
            deleted = mysql_history_delete(item_id)
        else:
            raise RuntimeError("remote history is not configured")
        return {"backend": config["backend"], "deleted": deleted}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"backend": "local", "detail": str(exc)},
        )


@app.delete("/api/history")
def clear_history():
    config = history_config()
    if not config["enabled"]:
        return JSONResponse(
            status_code=503,
            content={"backend": "local", "detail": "remote history is not configured"},
        )
    try:
        if config["backend"] == "redis":
            deleted = redis_history_clear()
        elif config["backend"] == "mysql":
            deleted = mysql_history_clear()
        else:
            raise RuntimeError("remote history is not configured")
        return {"backend": config["backend"], "deleted": deleted}
    except Exception as exc:
        return JSONResponse(
            status_code=503,
            content={"backend": "local", "detail": str(exc)},
        )


def run_command(command: list[str]):
    completed = subprocess.run(
        command,
        check=False,
        capture_output=True,
        text=True,
    )
    if completed.returncode != 0:
        error = completed.stderr.strip() or completed.stdout.strip()
        raise RuntimeError(error or f"命令执行失败: {' '.join(command)}")


def svg_dimensions(svg_content: str) -> tuple[int, int]:
    opening_tag = svg_content.split(">", 1)[0]
    attrs = {key.lower(): value for key, _, value in SVG_ATTR_RE.findall(opening_tag)}

    def parse_length(value: str | None) -> int | None:
        if not value:
            return None
        if "%" in value:
            return None
        match = re.match(r"^\s*([0-9.]+)", value)
        if not match:
            return None
        return int(float(match.group(1)))

    width = parse_length(attrs.get("width"))
    height = parse_length(attrs.get("height"))
    view_box = attrs.get("viewbox")
    if (not width or not height) and view_box:
        parts = view_box.replace(",", " ").split()
        if len(parts) == 4:
            width = width or int(float(parts[2]))
            height = height or int(float(parts[3]))

    return max(width or 900, 100), max(height or 500, 100)


def svg_viewbox(svg_content: str, width: int, height: int) -> tuple[float, float, float, float]:
    opening_tag = svg_content.split(">", 1)[0]
    attrs = {key.lower(): value for key, _, value in SVG_ATTR_RE.findall(opening_tag)}
    view_box = attrs.get("viewbox")
    if view_box:
        parts = view_box.replace(",", " ").split()
        if len(parts) == 4:
            return tuple(float(part) for part in parts)
    return 0, 0, float(width), float(height)


def pad_svg_for_docx(svg_content: str, padding: int = DOCX_SVG_PADDING) -> str:
    width, height = svg_dimensions(svg_content)
    min_x, min_y, view_width, view_height = svg_viewbox(svg_content, width, height)
    padded_min_x = min_x - padding
    padded_min_y = min_y - padding
    padded_width = view_width + padding * 2
    padded_height = view_height + padding * 2

    opening_tag, rest = svg_content.split(">", 1)
    if re.search(r"\bviewBox\s*=", opening_tag, re.IGNORECASE):
        opening_tag = re.sub(
            r"""\bviewBox\s*=\s*(['"]).*?\1""",
            f'viewBox="{padded_min_x:.2f} {padded_min_y:.2f} {padded_width:.2f} {padded_height:.2f}"',
            opening_tag,
            count=1,
            flags=re.IGNORECASE,
        )
    else:
        opening_tag += f' viewBox="{padded_min_x:.2f} {padded_min_y:.2f} {padded_width:.2f} {padded_height:.2f}"'

    opening_tag = re.sub(r"""\bwidth\s*=\s*(['"]).*?\1""", f'width="{padded_width:.0f}"', opening_tag, count=1, flags=re.IGNORECASE)
    opening_tag = re.sub(r"""\bheight\s*=\s*(['"]).*?\1""", f'height="{padded_height:.0f}"', opening_tag, count=1, flags=re.IGNORECASE)
    if not re.search(r"\boverflow\s*=", opening_tag, re.IGNORECASE):
        opening_tag += ' overflow="visible"'
    background = (
        f'<rect x="{padded_min_x:.2f}" y="{padded_min_y:.2f}" '
        f'width="{padded_width:.2f}" height="{padded_height:.2f}" fill="#ffffff" />'
    )
    return f"{opening_tag}>{background}{rest}"


def docx_display_size(width: int, height: int) -> tuple[int, int]:
    return image_display_size(width, height, DOCX_MAX_IMAGE_WIDTH)


def pdf_display_size(width: int, height: int) -> tuple[int, int]:
    return image_display_size(width, height, PDF_MAX_IMAGE_WIDTH)


def image_display_size(width: int, height: int, max_width: int) -> tuple[int, int]:
    if width <= max_width:
        return width, height
    ratio = max_width / width
    return max_width, max(int(height * ratio), 1)


def docx_render_size(width: int, height: int, image_scale: int) -> tuple[int, int]:
    scale = normalized_docx_image_scale(image_scale)
    render_width = width * scale
    render_height = height * scale
    if render_width <= DOCX_MAX_RENDER_SIDE and render_height <= DOCX_MAX_RENDER_SIDE:
        return render_width, render_height

    ratio = min(DOCX_MAX_RENDER_SIDE / render_width, DOCX_MAX_RENDER_SIDE / render_height)
    return max(int(render_width * ratio), 1), max(int(render_height * ratio), 1)


def normalized_docx_image_scale(value: int) -> int:
    return min(max(value, DOCX_IMAGE_SCALE_MIN), DOCX_IMAGE_SCALE_MAX)


def parse_simple_attrs(raw_attrs: str) -> dict[str, str]:
    return {key.lower(): value for key, _, value in SIMPLE_ATTR_RE.findall(raw_attrs)}


def parse_float(value: str | None, default: float = 0) -> float:
    if not value:
        return default
    match = re.match(r"^\s*(-?[0-9.]+)", value)
    return float(match.group(1)) if match else default


def foreign_object_text(content: str) -> str:
    content = re.sub(r"<br\s*/?>", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"</(div|p|li|tr|h[1-6])>", "\n", content, flags=re.IGNORECASE)
    content = re.sub(r"</(td|th)>", " ", content, flags=re.IGNORECASE)
    content = re.sub(r"<[^>]+>", "", content)
    lines = [" ".join(line.split()) for line in html_utils.unescape(content).splitlines()]
    return "\n".join(line for line in lines if line)


def foreign_object_font_size(content: str) -> int:
    style_match = re.search(r"font-size\s*:\s*([0-9.]+)px", content, re.IGNORECASE)
    if style_match:
        return int(float(style_match.group(1)))

    font_match = re.search(r"font\s*:\s*(?:[^;]*?\s)?([0-9.]+)px", content, re.IGNORECASE)
    if font_match:
        return int(float(font_match.group(1)))

    return 16


def text_width_units(value: str) -> float:
    units = 0.0
    for char in value:
        units += 0.95 if ord(char) > 127 else 0.48
    return units


def text_fits_width(text: str, max_width: float, font_size: int) -> bool:
    return text_width_units(text) * max(font_size, 1) <= max_width


def wrap_long_token(token: str, max_width: float, font_size: int) -> list[str]:
    lines: list[str] = []
    current = ""
    for char in token:
        candidate = f"{current}{char}"
        if current and not text_fits_width(candidate, max_width, font_size):
            lines.append(current)
            current = char
        else:
            current = candidate
    if current:
        lines.append(current)
    return lines


def wrap_text_to_width(text: str, max_width: float, font_size: int) -> list[str]:
    wrapped_lines: list[str] = []
    relaxed_width = max_width * 1.18

    for raw_line in text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        if text_fits_width(line, relaxed_width, font_size):
            wrapped_lines.append(line)
            continue

        chunks = re.findall(r"[^\s/_\-:]+[\s/_\-:]*", line)
        current = ""
        for chunk in chunks or [line]:
            candidate = f"{current}{chunk}"
            if text_fits_width(candidate, max_width, font_size):
                current = candidate
                continue

            if current:
                wrapped_lines.append(current.rstrip())
                current = ""

            if text_fits_width(chunk, max_width, font_size):
                current = chunk
            else:
                wrapped_lines.extend(wrap_long_token(chunk.rstrip(), max_width, font_size))

        if current:
            wrapped_lines.append(current.rstrip())

    return wrapped_lines or [text]


def foreign_objects_to_svg_text(svg_content: str) -> str:
    def replace_foreign_object(match: re.Match[str]) -> str:
        attrs = parse_simple_attrs(match.group(1))
        body = match.group(2)
        text = foreign_object_text(body)
        if not text:
            return ""

        x = parse_float(attrs.get("x"))
        y = parse_float(attrs.get("y"))
        width = parse_float(attrs.get("width"), 120)
        height = parse_float(attrs.get("height"), 24)
        font_size = foreign_object_font_size(body)
        text_padding = max(font_size * 0.25, 4)
        lines = wrap_text_to_width(text, width - text_padding * 2, font_size)
        line_height = font_size * 1.2
        if len(lines) * line_height > height - text_padding * 2:
            font_size = max(int((height - text_padding * 2) / (len(lines) * 1.2)), 9)
            line_height = font_size * 1.2
        first_line_y = y + height / 2 - line_height * (len(lines) - 1) / 2

        tspans = []
        for index, line in enumerate(lines):
            escaped_line = html_utils.escape(line)
            dy = 0 if index == 0 else line_height
            tspans.append(
                f'<tspan x="{x + width / 2:.2f}" dy="{dy:.2f}">{escaped_line}</tspan>'
            )

        return (
            f'<text x="{x + width / 2:.2f}" y="{first_line_y:.2f}" '
            f'font-family="Arial, sans-serif" font-size="{font_size}" '
            f'fill="#333333" text-anchor="middle" dominant-baseline="middle">'
            f'{"".join(tspans)}</text>'
        )

    return FOREIGN_OBJECT_RE.sub(replace_foreign_object, svg_content)


def data_images_to_files_for_docx(input_html: str, task_id: str) -> tuple[str, list[str], bool]:
    with open(input_html, "r", encoding="utf-8") as f:
        html_content = f.read()

    generated_files: list[str] = []
    converted = False

    def replace_data_image(match: re.Match[str]) -> str:
        nonlocal converted
        index = len(generated_files)
        extension = "jpg" if match.group(2).lower() in {"jpg", "jpeg"} else "png"
        image_file = f"/tmp/{task_id}-browser-diagram-{index}.{extension}"
        image_data = base64.b64decode(match.group(3))
        with open(image_file, "wb") as f:
            f.write(image_data)

        generated_files.append(image_file)
        converted = True
        return f"{match.group(1)}{html_utils.escape(image_file, quote=True)}{match.group(4)}"

    converted_html = DATA_IMAGE_RE.sub(replace_data_image, html_content)
    if not converted:
        return input_html, generated_files, False

    converted_file = f"/tmp/{task_id}-docx-data-images.html"
    with open(converted_file, "w", encoding="utf-8") as f:
        f.write(converted_html)

    generated_files.append(converted_file)
    return converted_file, generated_files, True


def svg_to_png_for_docx(
    input_html: str,
    task_id: str,
    image_scale: int,
    max_display_width: int = DOCX_MAX_IMAGE_WIDTH,
) -> tuple[str, list[str]]:
    with open(input_html, "r", encoding="utf-8") as f:
        html_content = f.read()

    image_scale = normalized_docx_image_scale(image_scale)
    generated_files: list[str] = []

    def replace_svg(match: re.Match[str]) -> str:
        index = len(generated_files) // 2
        svg_content = match.group(0)
        if "xmlns=" not in svg_content[:200]:
            svg_content = svg_content.replace("<svg", '<svg xmlns="http://www.w3.org/2000/svg"', 1)

        original_width, original_height = svg_dimensions(svg_content)
        svg_content = foreign_objects_to_svg_text(svg_content)
        svg_content = pad_svg_for_docx(svg_content)
        padded_width, padded_height = svg_dimensions(svg_content)
        display_width, display_height = image_display_size(original_width, original_height, max_display_width)
        render_width, render_height = docx_render_size(padded_width, padded_height, image_scale)
        svg_file = f"/tmp/{task_id}-diagram-{index}.svg"
        png_file = f"/tmp/{task_id}-diagram-{index}.png"
        with open(svg_file, "w", encoding="utf-8") as f:
            f.write(svg_content)

        run_command([
            "rsvg-convert",
            "-f",
            "png",
            "-w",
            str(render_width),
            "-h",
            str(render_height),
            "-o",
            png_file,
            svg_file,
        ])
        generated_files.extend([svg_file, png_file])
        escaped_png = html_utils.escape(png_file, quote=True)
        return (
            f'<img src="{escaped_png}" alt="diagram" '
            f'width="{display_width}" height="{display_height}" />'
        )

    converted_html = SVG_RE.sub(replace_svg, html_content)
    converted_file = f"/tmp/{task_id}-docx.html"
    with open(converted_file, "w", encoding="utf-8") as f:
        f.write(converted_html)

    generated_files.append(converted_file)
    return converted_file, generated_files


@app.post("/api/render")
async def render_markdown(
    background_tasks: BackgroundTasks,
    markdown_text: str = Form(None),
    html_text: str = Form(None),
    file: UploadFile = File(None),
    format: str = Form("pdf"),
    docx_image_scale: int = Form(DOCX_IMAGE_SCALE_DEFAULT),
):
    if format not in ALLOWED_FORMATS:
        raise HTTPException(status_code=400, detail=f"不支持的格式: {format}")

    if not markdown_text and not html_text and not file:
        raise HTTPException(status_code=400, detail="必须提供 markdown_text、html_text 或上传文件")

    task_id = str(uuid.uuid4())
    source_ext = "html" if html_text else "md"
    input_file = f"/tmp/{task_id}.{source_ext}"
    output_file = f"/tmp/{task_id}.{format}"

    if html_text:
        source_content = html_text
    elif file:
        source_content = (await file.read()).decode("utf-8")
    else:
        source_content = markdown_text

    with open(input_file, "w", encoding="utf-8") as f:
        f.write(source_content)

    files_to_cleanup = [input_file, output_file]

    try:
        if format == "html":
            if html_text:
                output_file = input_file
            else:
                pypandoc.convert_file(input_file, "html", outputfile=output_file)
        elif format == "docx":
            input_format = "html" if html_text else "md"
            docx_input_file = input_file
            if html_text:
                docx_input_file, generated_files, has_browser_images = data_images_to_files_for_docx(
                    input_file,
                    task_id,
                )
                if not has_browser_images:
                    docx_input_file, generated_files = svg_to_png_for_docx(
                        input_file,
                        task_id,
                        docx_image_scale,
                    )
                files_to_cleanup.extend(generated_files)
            pypandoc.convert_file(docx_input_file, "docx", format=input_format, outputfile=output_file)
        elif format == "pdf":
            pdf_input_file = input_file
            if html_text:
                pdf_input_file, generated_files, has_browser_images = data_images_to_files_for_docx(
                    input_file,
                    task_id,
                )
                files_to_cleanup.extend(generated_files)
                if not has_browser_images:
                    pdf_input_file, generated_files = svg_to_png_for_docx(
                        input_file,
                        task_id,
                        PDF_IMAGE_SCALE,
                        PDF_MAX_IMAGE_WIDTH,
                    )
                    files_to_cleanup.extend(generated_files)
            if html_text:
                run_command([
                    "wkhtmltopdf",
                    "--encoding",
                    "utf-8",
                    "--enable-local-file-access",
                    pdf_input_file,
                    output_file,
                ])
            else:
                pypandoc.convert_file(
                    input_file,
                    "pdf",
                    outputfile=output_file,
                    extra_args=[
                        "--pdf-engine=wkhtmltopdf",
                        "-V",
                        "margin-top=1in",
                        "-V",
                        "margin-bottom=1in",
                    ],
                )

        return FileResponse(
            output_file,
            filename=f"document.{format}",
            media_type=MEDIA_TYPES[format],
            background=background_tasks,
        )
    except Exception as exc:
        cleanup_files(files_to_cleanup)
        raise HTTPException(status_code=500, detail=f"渲染失败: {exc}") from exc
    finally:
        if not background_tasks.tasks:
            background_tasks.add_task(cleanup_files, files_to_cleanup)


@app.get("/health")
def health_check():
    return {"status": "ok"}
