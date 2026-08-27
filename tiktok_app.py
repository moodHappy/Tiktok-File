import os
import json
import base64
import re
import html
import requests
from datetime import datetime, timezone, timedelta

# ================= 配置区 =================
BASE_DIR = "docs"
REPO_NAME = "Tiktok-File"
tz_utc_8 = timezone(timedelta(hours=8))

# ================= 批注核心引擎 (注入单集精读) =================
ENGINE_SCRIPT = r"""
function renderMarkdown(text) {
    if (typeof marked === 'undefined') return text;
    let safeText = text.replace(/<script\b[^<]*(?:(?!<\/script>)<[^<]*)*<\/script>/gi, '')
                       .replace(/<iframe\b[^<]*(?:(?!<\/iframe>)<[^<]*)*<\/iframe>/gi, '')
                       .replace(/\bon[a-z]+\s*=/gi, 'data-blocked=');
    return marked.parse(safeText);
}

let syncTimeout = null;
function scheduleSync() {
    const statusMsg = document.getElementById('sync-status');
    statusMsg.style.display = 'inline-block';
    statusMsg.style.backgroundColor = '#f39c12';
    statusMsg.innerText = '⏳ 更改已记录，5秒后自动同步...';
    if (syncTimeout) clearTimeout(syncTimeout);
    syncTimeout = setTimeout(syncToGitHub, 5000);
}

const AI_PROMPT = `你是一位精通美国年轻一代流行语、TikTok 梗文化（Gen-Z Slang）的英语名师。
请分析以下 TikTok 英文评论，严格按照以下 Markdown 格式输出（不要输出任何多余废话）：

### 📌 地道中文翻译

[此处填写结合语境的地道口语化翻译]

### 📌 俚语与核心表达 (Slang & Expressions)

- **[单词/俚语/网络缩写]**
  = [中文释义]
  （[详细解析：包括缩写还原(如 fr=for real)、梗背景或地道使用场景]）

评论内容：
`;

async function fetchGroq(text, apiKey, modelName) {
    const res = await fetch('https://api.groq.com/openai/v1/chat/completions', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
            model: modelName,
            messages: [
                { role: 'system', content: 'You are an expert English teacher specialized in Gen-Z slang and internet culture.' },
                { role: 'user', content: AI_PROMPT + `"${text}"` }
            ],
            temperature: 0.3
        })
    });
    if (!res.ok) throw new Error(`Groq API Error: ${res.status}`);
    const json = await res.json();
    if (json.choices && json.choices.length > 0) return json.choices[0].message.content.trim();
    throw new Error('Groq返回数据异常');
}

async function fetchGLM(text, apiKey, modelName) {
    const res = await fetch('https://open.bigmodel.cn/api/paas/v4/chat/completions', {
        method: 'POST',
        headers: { 'Authorization': `Bearer ${apiKey}`, 'Content-Type': 'application/json' },
        body: JSON.stringify({
            model: modelName,
            messages: [
                { role: 'system', content: 'You are an expert English teacher specialized in Gen-Z slang and internet culture.' },
                { role: 'user', content: AI_PROMPT + `"${text}"` }
            ],
            temperature: 0.3
        })
    });
    if (!res.ok) throw new Error(`智谱GLM API Error: ${res.status}`);
    const json = await res.json();
    if (json.choices && json.choices.length > 0) return json.choices[0].message.content.trim();
    throw new Error('智谱GLM返回数据异常');
}

async function executeAIPipeline(text) {
    const pref = localStorage.getItem('PREFERRED_AI') || 'groq';
    const groqKey = localStorage.getItem('GROQ_API_KEY') || '';
    const glmKey = localStorage.getItem('GLM_API_KEY') || '';
    const groqModel = localStorage.getItem('GROQ_MODEL') || 'llama-3.3-70b-versatile';
    const glmModel = localStorage.getItem('GLM_MODEL') || 'GLM-4.5-Flash';

    if ((!groqKey && !glmKey) || (!groqModel && !glmModel)) throw new Error('MISSING_KEYS_OR_MODELS');

    const runGroq = async () => { if (!groqKey || !groqModel) throw new Error("Groq 配置缺失"); return await fetchGroq(text, groqKey, groqModel); };
    const runGLM = async () => { if (!glmKey || !glmModel) throw new Error("智谱GLM 配置缺失"); return await fetchGLM(text, glmKey, glmModel); };

    if (pref === 'groq') {
        try { return await runGroq(); } catch (err) {
            console.warn("Groq 失败，降级到智谱:", err);
            if (glmKey && glmModel) { document.getElementById('sync-status').innerText = '⚠️ 降级为智谱...'; return await runGLM(); }
            throw err;
        }
    } else {
        try { return await runGLM(); } catch (err) {
            console.warn("智谱 失败，降级到Groq:", err);
            if (groqKey && groqModel) { document.getElementById('sync-status').innerText = '⚠️ 降级为Groq...'; return await runGroq(); }
            throw err;
        }
    }
}

function initAnnotations() {
    document.querySelectorAll('.para-wrap').forEach(wrap => {
        const view = wrap.querySelector('.anno-view');
        const edit = wrap.querySelector('.anno-edit');
        const toggle = wrap.querySelector('.anno-toggle');
        const aiToggle = wrap.querySelector('.ai-toggle');
        const box = wrap.querySelector('.anno-box');

        const rawText = edit.value.trim();
        if (rawText) { toggle.classList.add('has-anno'); view.innerHTML = renderMarkdown(rawText); }
        
        if (aiToggle) {
            aiToggle.addEventListener('click', async (e) => {
                e.preventDefault(); e.stopPropagation();
                if (aiToggle.classList.contains('loading')) return;

                // 批注覆盖确认保护机制
                if (edit.value.trim() !== '') {
                    if (!confirm('⚠️ 该评论已有批注，是否让 AI 重新生成并覆盖原内容？\n(点击取消则保留原批注)')) {
                        return;
                    }
                }

                const groqKey = localStorage.getItem('GROQ_API_KEY') || '';
                const glmKey = localStorage.getItem('GLM_API_KEY') || '';
                if (!groqKey && !glmKey) { alert('⚠️ 请先返回日历配置中心设置 AI API Key！'); return; }

                const pClone = wrap.querySelector('.card-text').cloneNode(true);
                pClone.querySelectorAll('.anno-toggle, .ai-toggle').forEach(el => el.remove());
                const pText = pClone.textContent.trim();
                if (!pText) return;

                aiToggle.classList.add('loading');
                const statusMsg = document.getElementById('sync-status');
                statusMsg.style.display = 'inline-block';
                statusMsg.style.backgroundColor = '#fe2c55';
                statusMsg.innerText = '🤖 AI 拆解俚语中...';

                try {
                    const aiContent = await executeAIPipeline(pText);
                    box.style.display = 'block'; view.style.display = 'none'; edit.style.display = 'block';
                    edit.value = aiContent; edit.focus(); edit.blur();
                    statusMsg.style.backgroundColor = '#2ea44f'; statusMsg.innerText = '✅ 解析成功';
                    setTimeout(() => { if (statusMsg.innerText.includes('成功')) statusMsg.style.display = 'none'; }, 2000);
                } catch (err) {
                    console.error(err);
                    alert(err.message === 'MISSING_KEYS_OR_MODELS' ? '⚠️ 请返回配置AI密钥和模型！' : '❌ AI 解析失败: ' + err.message);
                    statusMsg.style.display = 'none';
                } finally { aiToggle.classList.remove('loading'); }
            });
        }

        toggle.addEventListener('click', (e) => {
            e.preventDefault(); e.stopPropagation();
            if (box.style.display === 'block') { box.style.display = 'none'; } 
            else {
                box.style.display = 'block';
                if (!edit.value.trim()) { view.style.display = 'none'; edit.style.display = 'block'; setTimeout(() => edit.focus(), 50); } 
                else { view.style.display = 'block'; edit.style.display = 'none'; }
            }
        });

        const triggerEdit = () => { view.style.display = 'none'; edit.style.display = 'block'; edit.value = edit.value; setTimeout(() => edit.focus(), 50); };
        view.addEventListener('dblclick', () => { box.style.display = 'none'; });

        let lastTap = 0;
        view.addEventListener('touchstart', e => {
            if (e.touches.length === 2) { triggerEdit(); } 
            else if (e.touches.length === 1) {
                const currentTime = new Date().getTime();
                const tapLength = currentTime - lastTap;
                if (tapLength < 500 && tapLength > 0) { box.style.display = 'none'; }
                lastTap = currentTime;
            }
        }, {passive: true});

        edit.addEventListener('blur', () => {
            const newVal = edit.value.trim();
            try { view.innerHTML = newVal ? renderMarkdown(newVal) : ''; } catch(e){}
            edit.style.display = 'none';
            if (newVal) { view.style.display = 'block'; toggle.classList.add('has-anno'); } 
            else { view.style.display = 'none'; box.style.display = 'none'; toggle.classList.remove('has-anno'); }

            if (edit.getAttribute('data-old-val') !== newVal) {
                edit.setAttribute('data-old-val', newVal);
                scheduleSync();
            }
        });
        edit.setAttribute('data-old-val', rawText);
    });
}
window.onload = initAnnotations;

function escapeHTML(str) {
    if (typeof str !== 'string') return '';
    return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
}

function reconstructSelfHTML() {
    const dataTag = document.getElementById('page-data');
    if (!dataTag) throw new Error("Missing state data!");
    const pageData = JSON.parse(dataTag.textContent);
    
    document.querySelectorAll('.chat-message').forEach((msg, idx) => {
        const edit = msg.querySelector('.anno-edit');
        if (pageData.comments[idx] && edit) {
            pageData.comments[idx].annotation = edit.value || "";
        }
    });

    const newJsonStr = JSON.stringify(pageData).replace(/</g, '\\u003c');
    const engineText = document.getElementById('matrix-engine').textContent;
    const styleText = document.querySelector('style').textContent;
    const titleText = document.title;

    let comments_html = "";
    pageData.comments.forEach(c => {
        comments_html += `
        <div class="chat-message">
            <img src="${escapeHTML(c.avatar)}" class="avatar" alt="avatar" loading="lazy">
            <div class="message-content">
                <div class="message-header">
                    <span class="author">${escapeHTML(c.author)}</span>
                    <span class="likes">❤️ ${escapeHTML(c.likes_str)}</span>
                </div>
                <div class="para-wrap">
                    <div class="bubble card-text">${escapeHTML(c.text)}<span class="anno-toggle" title="点击添加/查看批注">🔴</span><span class="ai-toggle" title="AI俚语解析">🤖</span></div>
                    <div class="anno-box" style="display:none;">
                        <div class="anno-view markdown-body"></div>
                        <textarea class="anno-edit" style="display:none;" placeholder="在此记录该评论的俚语拆解或灵感...">${escapeHTML(c.annotation)}</textarea>
                    </div>
                </div>
            </div>
        </div>`;
    });

    const cleanHTML = `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>${escapeHTML(titleText)}</title>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"><\/script>
    <style>${styleText}</style>
</head>
<body>
    <div class="nav-back">
        <a href="../../index.html">🔙 返回日曆樞紐</a>
        <span id="sync-status" class="sync-status"></span>
    </div>
    <div class="container">
        <h2 style="text-align: center; margin-bottom: 25px; color: #333;">📅 ${pageData.year}-${String(pageData.month).padStart(2,'0')}-${String(pageData.day).padStart(2,'0')}</h2>
        <div class="video-card">
            <a href="${escapeHTML(pageData.video.url)}" target="_blank"><img src="${escapeHTML(pageData.video.thumb)}" class="video-thumb" alt="Thumbnail"></a>
            <div class="video-info">
                <span class="v-channel">${escapeHTML(pageData.video.channel)}</span>
                <h1 class="v-title">${escapeHTML(pageData.video.title)}</h1>
                <div class="v-actions">
                    <span class="timestamp">更新於: ${escapeHTML(pageData.video.now_str)}</span>
                    <a href="${escapeHTML(pageData.video.url)}" target="_blank" class="btn-play">▶ 原片</a>
                </div>
            </div>
        </div>
        <div class="chat-container">
            ${comments_html ? comments_html : '<div class="empty-state">暫無高价值评论。</div>'}
        </div>
    </div>
    <script id="page-data" type="application/json">${newJsonStr}<\/script>
    <script id="matrix-engine">${engineText}<\/script>
</body>
</html>`;
    return cleanHTML;
}

async function syncToGitHub() {
    const token = localStorage.getItem('GH_TOKEN');
    const owner = localStorage.getItem('GH_OWNER');
    const repo = 'Tiktok-File';
    
    if(!token || !owner) { alert('缺少 GitHub Token，无法同步！'); return; }

    const statusMsg = document.getElementById('sync-status');
    statusMsg.style.display = 'inline-block';
    statusMsg.style.backgroundColor = '#2ea44f';
    statusMsg.innerText = '📡 同步中...';

    const pureHtml = reconstructSelfHTML();
    let urlPath = window.location.pathname;
    const match = urlPath.match(/(\d{4}\/\d{1,2}\/[^/]+\.html)$/);
    let fileRelPath = match ? "docs/" + match[1] : (urlPath.includes('docs/') ? urlPath.substring(urlPath.indexOf('docs/')) : null);
    
    if (!fileRelPath) { alert('路径解析失败！'); statusMsg.style.display = 'none'; return; }

    try {
        const base64Html = btoa(encodeURIComponent(pureHtml).replace(/%([0-9A-F]{2})/g, function(match, p1) { return String.fromCharCode('0x' + p1); }));
        const getRes = await fetch(`https://api.github.com/repos/${owner}/${repo}/contents/${fileRelPath}?t=${Date.now()}`, { headers: { 'Authorization': `token ${token}` }, cache: 'no-store' });
        if (!getRes.ok) throw new Error('API 获取 SHA 失败');
        const fileData = await getRes.json();
        const putRes = await fetch(`https://api.github.com/repos/${owner}/${repo}/contents/${fileRelPath}`, {
            method: 'PUT',
            headers: { 'Authorization': `token ${token}`, 'Content-Type': 'application/json' },
            body: JSON.stringify({ message: `Auto-save annotation`, content: base64Html, sha: fileData.sha })
        });
        if(putRes.ok) {
            statusMsg.style.backgroundColor = '#2ea44f'; statusMsg.innerText = '✅ 云端已同步';
            setTimeout(() => { if (statusMsg.innerText === '✅ 云端已同步') statusMsg.style.display = 'none'; }, 3000);
        } else throw new Error('Put 请求失败');
    } catch(e) {
        statusMsg.style.backgroundColor = '#e74c3c'; statusMsg.innerText = '❌ 同步失败(点击重试)';
        statusMsg.style.cursor = 'pointer';
        statusMsg.onclick = () => { statusMsg.onclick = null; statusMsg.style.cursor = 'default'; syncToGitHub(); };
    }
}
"""

def generate_index_template():
    """扫描本地 docs 目录并更新主页日历 index.html"""
    archive_data = {}
    if os.path.exists(BASE_DIR):
        years = [d for d in os.listdir(BASE_DIR) if d.isdigit()]
        for year in years:
            months = [d for d in os.listdir(os.path.join(BASE_DIR, year)) if d.isdigit()]
            for month in months:
                files = sorted([f for f in os.listdir(os.path.join(BASE_DIR, year, month)) if f.endswith('.html')], reverse=True)
                for file in files:
                    try:
                        parts = file.replace(".html", "").split('_')
                        if len(parts) >= 4:
                            f_year, f_month, f_day = str(int(parts[0])), str(int(parts[1])), str(int(parts[2]))
                            time_str = f"{parts[3][:2]}:{parts[3][2:4]}"
                            file_path = f"{year}/{month}/{file}"
                            title = "🎵 TikTok 单集精读"

                            if f_year not in archive_data: archive_data[f_year] = {}
                            if f_month not in archive_data[f_year]: archive_data[f_year][f_month] = {}
                            if f_day not in archive_data[f_year][f_month]: archive_data[f_year][f_month][f_day] = []

                            archive_data[f_year][f_month][f_day].append({
                                "time": time_str,
                                "path": file_path,
                                "title": title
                            })
                    except Exception:
                        pass

    json_data = json.dumps(archive_data)
    engine_b64 = base64.b64encode(ENGINE_SCRIPT.encode('utf-8')).decode('utf-8')

    html_template = r"""<!DOCTYPE html>
<html lang="zh-CN">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>TikTok 潮语精读日历</title>
    <style>
        :root { --bg: #f5f5f7; --text: #333; --muted: #888; --primary: #fe2c55; --border: #e0e0e0; --card: #fff; }
        body, html { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif; -webkit-font-smoothing: antialiased; background: var(--bg); margin: 0; padding: 0; color: var(--text); }
        .container { max-width: 600px; margin: 0 auto; padding-bottom: 20px; }
        
        .manual-fetch-bar { background: var(--card); padding: 12px 15px; display: flex; gap: 10px; align-items: center; border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 20; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }
        .fetch-input { flex: 1; padding: 10px 15px; border: 1px solid #ccc; border-radius: 20px; font-size: 14px; outline: none; background: #f9f9f9; transition: border 0.2s; }
        .fetch-input:focus { border-color: var(--primary); background: #fff; }
        .settings-btn { background: none; border: none; font-size: 20px; cursor: pointer; padding: 5px; }
        
        .modal-overlay { display: none; position: fixed; top: 0; left: 0; right: 0; bottom: 0; background: rgba(0,0,0,0.5); z-index: 100; justify-content: center; align-items: center; padding: 20px; }
        .modal-content { background: var(--card); border-radius: 16px; padding: 20px; width: 100%; max-width: 400px; box-shadow: 0 10px 30px rgba(0,0,0,0.1); max-height: 85vh; overflow-y: auto; }
        .modal-title { margin: 0 0 15px 0; font-size: 18px; font-weight: bold; }
        .form-group { margin-bottom: 15px; }
        .form-group label { display: block; font-size: 13px; color: var(--muted); margin-bottom: 5px; font-weight: bold; }
        .form-group input, .form-group select { width: 100%; box-sizing: border-box; padding: 10px; border: 1px solid #ddd; border-radius: 8px; font-size: 14px; outline: none; background: #fff; color: #333; }
        /* 强制修复 select 的下拉箭头和选中状态显示 */
        .form-group select { -webkit-appearance: menulist; appearance: menulist; cursor: pointer; }
        .modal-actions { display: flex; justify-content: flex-end; gap: 10px; margin-top: 20px; }
        .btn { padding: 8px 16px; border-radius: 8px; border: none; font-size: 14px; font-weight: bold; cursor: pointer; }
        .btn-cancel { background: #eee; color: #333; }
        .btn-save { background: var(--primary); color: #fff; }
        
        .controls { background: var(--bg); padding: 15px 20px; display: flex; justify-content: center; align-items: center; gap: 8px; border-bottom: 1px solid var(--border); }
        .control-btn { background: var(--primary); color: #fff; border: none; border-radius: 6px; padding: 8px 12px; font-size: 14px; cursor: pointer; font-weight: bold; transition: all 0.2s; }
        .control-btn:active { opacity: 0.8; transform: scale(0.95); }
        .select-box { padding: 6px 10px; border: 1px solid var(--border); border-radius: 6px; font-size: 15px; background: #fff; outline: none; font-weight: bold; cursor: pointer; color: #333;}
        .calendar-wrapper { background: var(--card); padding: 15px; margin-bottom: 15px; box-shadow: 0 1px 3px rgba(0,0,0,0.02); }
        .weekdays { display: grid; grid-template-columns: repeat(7, 1fr); text-align: center; font-weight: bold; font-size: 13px; color: var(--muted); margin-bottom: 10px; padding-bottom: 10px; border-bottom: 1px solid #f0f0f0; }
        .days-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 5px; }
        .day-cell { aspect-ratio: 1; display: flex; flex-direction: column; justify-content: center; align-items: center; font-size: 16px; font-weight: 600; border-radius: 10px; cursor: pointer; position: relative; transition: all 0.2s; }
        .day-cell.empty { visibility: hidden; }
        .day-cell.has-news { color: var(--text); }
        .day-cell.no-news { color: #ccc; }
        .day-cell.selected { background: #ffe5e8; border: 1px solid var(--primary); color: var(--primary); font-weight: bold; }
        .day-cell.today { background: #f0f0f0; color: #333; }
        .dot { width: 5px; height: 5px; background-color: var(--primary); border-radius: 50%; position: absolute; bottom: 6px; display: none; }
        .day-cell.has-news .dot { display: block; }
        
        .news-section { padding: 0 15px; }
        .news-item-wrapper { display: flex; align-items: center; gap: 10px; margin-bottom: 12px; }
        .news-item { flex: 1; background: var(--card); border-radius: 14px; padding: 18px 16px; display: flex; align-items: center; text-decoration: none; color: var(--text); box-shadow: 0 2px 8px rgba(0,0,0,0.03); border-left: 4px solid var(--primary); transition: all 0.2s; overflow: hidden; }
        .news-item:active { transform: scale(0.98); background: #fafafa; }
        .news-title { font-size: 15px; color: #333; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; font-weight: bold; flex: 1; }
        .delete-btn { background: #ff3b30; color: white; border: none; border-radius: 10px; padding: 0 15px; height: 54px; font-size: 16px; cursor: pointer; display: none; transition: all 0.2s; flex-shrink: 0; }
        
        .empty-state { text-align: center; padding: 40px 20px; color: var(--muted); font-size: 14px; background: var(--card); border-radius: 14px; }
        #loadingBar { height: 3px; background: var(--primary); width: 0%; transition: width 0.3s; position: absolute; top: 0; left: 0; z-index: 30; }
    </style>
</head>
<body>
    <div id="loadingBar"></div>
    <div class="manual-fetch-bar">
        <input type="text" id="tiktokUrlInput" class="fetch-input" placeholder="粘贴 TikTok 任意链接 (支持 vt.tiktok.com 短链)，回车抓取..." autocomplete="off">
        <button class="settings-btn" id="openSettingsBtn">⚙️</button>
    </div>

    <div class="modal-overlay" id="settingsModal">
        <div class="modal-content">
            <h3 class="modal-title">本地配置中心</h3>
            <p style="font-size:12px; color:#888; margin-top:-10px; margin-bottom:15px;">密钥保存在本地浏览器中，绝不会硬编码泄露在仓库代码中。</p>
            
            <div class="form-group"><label>RapidAPI Key (TikTok 数据源)</label><input type="password" id="cfgRapidKey" placeholder="例如 a52da3c..."></div>
            <div class="form-group"><label>RapidAPI Host (默认 tiktok-api23)</label><input type="text" id="cfgRapidHost" placeholder="tiktok-api23.p.rapidapi.com"></div>
            
            <div style="border-top:1px dashed #ddd; margin: 15px 0;"></div>
            <div class="form-group"><label>GitHub Personal Access Token</label><input type="password" id="cfgGhToken" placeholder="ghp_..."></div>
            <div class="form-group" style="display:flex; gap:10px;">
                <div style="flex:1;"><label>GitHub 用户名</label><input type="text" id="cfgGhOwner" placeholder="例如 moodHappy"></div>
                <div style="flex:1;"><label>仓库 (写死)</label><input type="text" value="Tiktok-File" readonly disabled style="background:#eee; color:#888;"></div>
            </div>

            <div style="border-top:1px dashed #ddd; margin: 15px 0;"></div>
            <div class="form-group"><label>首选 AI 引擎 (批注助手)</label><select id="cfgPrefAI"><option value="groq">Groq</option><option value="glm">智谱</option></select></div>
            <div class="form-group" style="display:flex; gap:10px;">
                <div style="flex:1;"><label>Groq Key</label><input type="password" id="cfgGroq" placeholder="gsk_..."></div>
                <div style="flex:1;"><label>Groq 模型</label><input type="text" id="cfgGroqModel" placeholder="llama-3.3-70b-versatile"></div>
            </div>
            <div class="form-group" style="display:flex; gap:10px;">
                <div style="flex:1;"><label>智谱 Key</label><input type="password" id="cfgGLM" placeholder="..."></div>
                <div style="flex:1;"><label>智谱 模型</label><input type="text" id="cfgGLMModel" placeholder="GLM-4.5-Flash"></div>
            </div>
            
            <div class="modal-actions">
                <button class="btn btn-cancel" id="closeSettingsBtn">取消</button>
                <button class="btn btn-save" id="saveSettingsBtn">保存配置</button>
            </div>
        </div>
    </div>

    <script>
        // 优雅的全局 Toast 提示组件
        function showToast(msg) {
            const toast = document.createElement('div');
            toast.textContent = msg;
            toast.style.cssText = 'position:fixed; top:20px; left:50%; transform:translateX(-50%); background:#2ea44f; color:#fff; padding:10px 20px; border-radius:20px; font-size:14px; font-weight:bold; z-index:9999; box-shadow:0 4px 12px rgba(0,0,0,0.15); opacity:0; transition:opacity 0.3s; pointer-events:none;';
            document.body.appendChild(toast);
            setTimeout(() => toast.style.opacity = '1', 10);
            setTimeout(() => {
                toast.style.opacity = '0';
                setTimeout(() => toast.remove(), 300);
            }, 2000);
        }

        const archiveData = /*DATA_START*/REPLACEME_JSON_DATA/*DATA_END*/;
        const today = new Date();
        const AppState = { year: today.getFullYear(), month: today.getMonth() + 1, day: today.getDate(), deleteMode: false };

        function initSelects() {
            const yearSelect = document.getElementById('yearSelect');
            yearSelect.innerHTML = '';
            const allYears = new Set(Object.keys(archiveData).map(Number));
            for(let i = -5; i <= 50; i++) allYears.add(today.getFullYear() + i);
            Array.from(allYears).sort((a, b) => b - a).forEach(y => { 
                const opt = document.createElement('option'); 
                opt.value = y; opt.textContent = y + ' 年'; 
                yearSelect.appendChild(opt); 
            });
        }

        function forceRender() {
            const maxDay = new Date(AppState.year, AppState.month, 0).getDate();
            if (AppState.day > maxDay) AppState.day = maxDay;

            document.getElementById('yearSelect').value = AppState.year;
            document.getElementById('monthSelect').value = AppState.month;

            const daysGrid = document.getElementById('daysGrid');
            const newsList = document.getElementById('newsList');
            daysGrid.innerHTML = ''; newsList.innerHTML = '';

            try {
                const firstDay = new Date(AppState.year, AppState.month - 1, 1).getDay() || 7;
                for (let i = 1; i < firstDay; i++) { 
                    const emptyCell = document.createElement('div'); 
                    emptyCell.className = 'day-cell empty'; 
                    daysGrid.appendChild(emptyCell); 
                }
                
                const monthData = (archiveData[AppState.year] && archiveData[AppState.year][AppState.month]) || {};
                
                for (let day = 1; day <= maxDay; day++) {
                    const cell = document.createElement('div'); cell.className = 'day-cell'; cell.textContent = day;
                    const dot = document.createElement('div'); dot.className = 'dot'; cell.appendChild(dot);
                    
                    if (monthData[day] && monthData[day].length > 0) cell.classList.add('has-news'); else cell.classList.add('no-news');
                    if (AppState.year === today.getFullYear() && AppState.month === today.getMonth() + 1 && day === today.getDate()) cell.classList.add('today');
                    if (day === AppState.day) cell.classList.add('selected');
                    
                    cell.onclick = () => { AppState.day = day; forceRender(); };
                    daysGrid.appendChild(cell);
                }
            } catch (err) {}

            try {
                let dayData = null;
                if (archiveData[AppState.year] && archiveData[AppState.year][AppState.month] && archiveData[AppState.year][AppState.month][AppState.day]) {
                    dayData = archiveData[AppState.year][AppState.month][AppState.day];
                }
                
                if (dayData && Array.isArray(dayData) && dayData.length > 0) {
                    dayData.forEach((news, index) => {
                        const wrapper = document.createElement('div'); wrapper.className = 'news-item-wrapper';
                        const a = document.createElement('a'); a.href = news.path; a.className = 'news-item';
                        a.innerHTML = `<span class="news-title" style="color: var(--primary);">${news.title} (${news.time})</span>`;
                        wrapper.appendChild(a);

                        const delBtn = document.createElement('button'); delBtn.className = 'delete-btn'; delBtn.innerHTML = '🗑️';
                        if (AppState.deleteMode) delBtn.style.display = 'block';
                        
                        delBtn.onclick = async (e) => {
                            e.preventDefault();
                            if(confirm('确认删除此条目并同步删除云端文件吗？')) {
                                const pathToDelete = news.path; 
                                dayData.splice(index, 1);
                                if (dayData.length === 0) delete archiveData[AppState.year][AppState.month][AppState.day];
                                forceRender(); 
                                await syncDeleteToGithub(pathToDelete);
                            }
                        };
                        wrapper.appendChild(delBtn); newsList.appendChild(wrapper);
                    });
                } else { 
                    newsList.innerHTML = '<div class="empty-state">当日暂无 TikTok 归档记录 👀</div>'; 
                }
            } catch (err) {}
        }

        document.getElementById('yearSelect').addEventListener('change', (e) => { AppState.year = parseInt(e.target.value, 10); forceRender(); });
        document.getElementById('monthSelect').addEventListener('change', (e) => { AppState.month = parseInt(e.target.value, 10); forceRender(); });
        document.getElementById('prevBtn').addEventListener('click', () => { AppState.month--; if (AppState.month < 1) { AppState.month = 12; AppState.year--; } forceRender(); });
        document.getElementById('nextBtn').addEventListener('click', () => { AppState.month++; if (AppState.month > 12) { AppState.month = 1; AppState.year++; } forceRender(); });
        document.getElementById('todayBtn').addEventListener('click', () => { AppState.year = today.getFullYear(); AppState.month = today.getMonth() + 1; AppState.day = today.getDate(); forceRender(); });

        let lastTap = 0;
        document.querySelector('.calendar-wrapper').addEventListener('click', (e) => {
            const tapLength = new Date().getTime() - lastTap;
            if (tapLength < 500 && tapLength > 0) {
                AppState.deleteMode = !AppState.deleteMode;
                document.querySelectorAll('.delete-btn').forEach(btn => btn.style.display = AppState.deleteMode ? 'block' : 'none');
                e.preventDefault();
            }
            lastTap = new Date().getTime();
        });

        initSelects(); forceRender();

        document.getElementById('openSettingsBtn').addEventListener('click', () => {
            document.getElementById('cfgRapidKey').value = localStorage.getItem('RAPIDAPI_KEY') || '';
            document.getElementById('cfgRapidHost').value = localStorage.getItem('RAPIDAPI_HOST') || 'tiktok-api23.p.rapidapi.com';
            document.getElementById('cfgGhToken').value = localStorage.getItem('GH_TOKEN') || '';
            document.getElementById('cfgGhOwner').value = localStorage.getItem('GH_OWNER') || '';
            document.getElementById('cfgGroq').value = localStorage.getItem('GROQ_API_KEY') || '';
            document.getElementById('cfgGroqModel').value = localStorage.getItem('GROQ_MODEL') || 'llama-3.3-70b-versatile';
            document.getElementById('cfgGLM').value = localStorage.getItem('GLM_API_KEY') || '';
            document.getElementById('cfgGLMModel').value = localStorage.getItem('GLM_MODEL') || 'GLM-4.5-Flash';
            
            // 确保下拉框有默认选中，且不会显示空白
            const savedAI = localStorage.getItem('PREFERRED_AI');
            const selectEl = document.getElementById('cfgPrefAI');
            if (savedAI === 'glm' || savedAI === 'groq') {
                selectEl.value = savedAI;
            } else {
                selectEl.value = 'groq';
            }
            
            document.getElementById('settingsModal').style.display = 'flex';
        });

        document.getElementById('closeSettingsBtn').addEventListener('click', () => { document.getElementById('settingsModal').style.display = 'none'; });

        document.getElementById('saveSettingsBtn').addEventListener('click', () => {
            localStorage.setItem('RAPIDAPI_KEY', document.getElementById('cfgRapidKey').value.trim());
            localStorage.setItem('RAPIDAPI_HOST', document.getElementById('cfgRapidHost').value.trim() || 'tiktok-api23.p.rapidapi.com');
            localStorage.setItem('GH_TOKEN', document.getElementById('cfgGhToken').value.trim());
            localStorage.setItem('GH_OWNER', document.getElementById('cfgGhOwner').value.trim());
            
            let prefAI = document.getElementById('cfgPrefAI').value;
            localStorage.setItem('PREFERRED_AI', prefAI ? prefAI : 'groq');
            
            localStorage.setItem('GROQ_API_KEY', document.getElementById('cfgGroq').value.trim());
            localStorage.setItem('GROQ_MODEL', document.getElementById('cfgGroqModel').value.trim());
            localStorage.setItem('GLM_API_KEY', document.getElementById('cfgGLM').value.trim());
            localStorage.setItem('GLM_MODEL', document.getElementById('cfgGLMModel').value.trim());
            
            document.getElementById('settingsModal').style.display = 'none';
            // 使用优雅的 Toast 提示代替 alert
            showToast('✅ 配置已成功保存！');
        });

        async function syncDeleteToGithub(fileRelPath) {
            const ghToken = localStorage.getItem('GH_TOKEN');
            const ghOwner = localStorage.getItem('GH_OWNER');
            const ghRepo = 'Tiktok-File';
            
            if (!ghToken || !ghOwner) return;
            try {
                const loadingBar = document.getElementById('loadingBar'); loadingBar.style.width = '10%';
                const targetFilePath = `docs/${fileRelPath}`;
                const fileRes = await fetch(`https://api.github.com/repos/${ghOwner}/${ghRepo}/contents/${targetFilePath}`, { headers: { 'Authorization': `token ${ghToken}` } });
                
                if (fileRes.ok) {
                    const fileData = await fileRes.json();
                    await fetch(`https://api.github.com/repos/${ghOwner}/${ghRepo}/contents/${targetFilePath}`, {
                        method: 'DELETE',
                        headers: { 'Authorization': `token ${ghToken}`, 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message: `Delete archived tiktok file: ${fileRelPath}`, sha: fileData.sha })
                    });
                }
                
                loadingBar.style.width = '50%';
                const idxRes = await fetch(`https://api.github.com/repos/${ghOwner}/${ghRepo}/contents/docs/index.html`, { headers: { 'Authorization': `token ${ghToken}` } });
                const idxData = await idxRes.json();
                const idxContent = decodeURIComponent(escape(atob(idxData.content)));
                const dataStart = idxContent.indexOf('/*DATA_START*/') + 14;
                const dataEnd = idxContent.indexOf('/*DATA_END*/');
                const newJsonStr = JSON.stringify(archiveData);
                const newIdxContent = idxContent.substring(0, dataStart) + newJsonStr + idxContent.substring(dataEnd);

                loadingBar.style.width = '80%';
                await fetch(`https://api.github.com/repos/${ghOwner}/${ghRepo}/contents/docs/index.html`, {
                    method: 'PUT',
                    headers: { 'Authorization': `token ${ghToken}`, 'Content-Type': 'application/json' },
                    body: JSON.stringify({ message: `Update index.html after deleting file`, content: btoa(unescape(encodeURIComponent(newIdxContent))), sha: idxData.sha })
                });
                
                loadingBar.style.width = '100%'; setTimeout(() => { loadingBar.style.width = '0%'; }, 1000);
            } catch(e) {}
        }

        // ================= 终极 TikTok 短链/长链多通道解析引擎 =================
        async function resolveTikTokVideoData(rawInput) {
            let text = rawInput.trim();
            
            // 1. 如果输入为纯数字 Video ID
            if (/^\d{15,22}$/.test(text)) return { id: text };
            
            // 2. 如果是标准完整长链 (/video/731130232...)
            let match = text.match(/\/video\/(\d{15,22})/);
            if (match) return { id: match[1] };

            // 3. 增强正则：匹配游离的15到22位数字ID
            let matchAlt = text.match(/\b\d{15,22}\b/);
            if (matchAlt) return { id: matchAlt[0] };

            // 4. 如果是 vt.tiktok.com 或 vm.tiktok.com 或 v.douyin.com 等短链
            if (text.includes('tiktok.com') || text.includes('douyin.com')) {
                
                // 【通道一】：Lovetik 极速解析引擎 (最强无头解析，专治短链跨域)
                try {
                    const formData = new URLSearchParams();
                    formData.append('query', text);
                    const res = await fetch('https://lovetik.com/api/ajax/search', {
                        method: 'POST',
                        headers: { 'Content-Type': 'application/x-www-form-urlencoded' },
                        body: formData.toString()
                    });
                    if (res.ok) {
                        const json = await res.json();
                        if (json && json.vid) {
                            return {
                                id: String(json.vid),
                                title: json.desc || '',
                                channel: json.author ? '@' + json.author : '',
                                thumb: json.cover || ''
                            };
                        }
                    }
                } catch (e) { console.warn("Lovetik 通道受阻:", e); }

                // 【通道二】：TikWM 直解析引擎 (原主力，作为备用)
                try {
                    const res = await fetch(`https://www.tikwm.com/api/?url=${encodeURIComponent(text)}`);
                    if (res.ok) {
                        const json = await res.json();
                        if (json && json.data && json.data.id) {
                            return {
                                id: String(json.data.id),
                                title: json.data.title || '',
                                channel: json.data.author ? '@' + (json.data.author.nickname || json.data.author.unique_id) : '',
                                thumb: json.data.cover || json.data.origin_cover || ''
                            };
                        }
                    }
                } catch (e) { console.warn("TikWM 通道受阻:", e); }
                
                // 【通道三】：Codetabs Proxy 网页源码正则反查 (高成功率兜底)
                try {
                    const res = await fetch(`https://api.codetabs.com/v1/proxy?quest=${encodeURIComponent(text)}`);
                    if (res.ok) {
                        const html = await res.text();
                        const m = html.match(/"aweme_id":"(\d{15,22})"/);
                        if (m) return { id: m[1] };
                    }
                } catch (e) { console.warn("Codetabs 通道受阻:", e); }

                // 【通道四】：AllOrigins 深层 HTML 解包 (借助官方 oEmbed 穿透)
                try {
                    const oembedUrl = `https://www.tiktok.com/oembed?url=${encodeURIComponent(text)}`;
                    const res = await fetch(`https://api.allorigins.win/get?url=${encodeURIComponent(oembedUrl)}`);
                    if (res.ok) {
                        const json = await res.json();
                        if (json && json.contents) {
                            const oData = JSON.parse(json.contents);
                            let embedUrl = oData.embed_url || oData.html || "";
                            const m = embedUrl.match(/\/video\/(\d{15,22})/);
                            if (m) return { id: m[1] };
                        }
                    }
                } catch (e) { console.warn("oEmbed 通道受阻:", e); }

                // 【通道五】：Unshorten 简单重定向反查
                try {
                    const res = await fetch(`https://unshorten.me/json/${encodeURIComponent(text)}`);
                    if (res.ok) {
                        const json = await res.json();
                        if (json && json.resolved_url) {
                            const m = json.resolved_url.match(/\/video\/(\d{15,22})/);
                            if (m) return { id: m[1] };
                        }
                    }
                } catch (e) {}
            }
            return null;
        }

        document.getElementById('tiktokUrlInput').addEventListener('keypress', async function (e) {
            if (e.key === 'Enter') {
                const url = this.value.trim();
                if (!url) return;

                const rapidKey = localStorage.getItem('RAPIDAPI_KEY');
                const rapidHost = localStorage.getItem('RAPIDAPI_HOST') || 'tiktok-api23.p.rapidapi.com';
                const ghToken = localStorage.getItem('GH_TOKEN');
                const ghOwner = localStorage.getItem('GH_OWNER');
                const ghRepo = 'Tiktok-File';
                
                if (!rapidKey || !ghToken || !ghOwner) {
                    alert('⚠️ 请先点击齿轮 ⚙️ 配置 RapidAPI Key 和 GitHub Token！');
                    document.getElementById('settingsModal').style.display = 'flex';
                    return;
                }

                const loadingBar = document.getElementById('loadingBar');
                loadingBar.style.width = '15%'; 
                this.disabled = true;

                try {
                    // 调用多通道短链解析引擎
                    const videoMeta = await resolveTikTokVideoData(url);
                    if (!videoMeta || !videoMeta.id) {
                        throw new Error("无法从该短链中解析出 Video ID。请在手机/电脑浏览器中打开该短链，复制跳转后的完整长链接重新粘贴！");
                    }

                    const videoId = videoMeta.id;
                    loadingBar.style.width = '35%';
                    
                    let videoTitle = videoMeta.title || `TikTok Video ${videoId}`;
                    let videoChannel = videoMeta.channel || "@TikTok Creator";
                    let videoCover = videoMeta.thumb || "https://p16-va.tiktokcdn.com/obj/tos-maliva-p-0068/default_cover.jpeg";
                    let videoUrl = `https://www.tiktok.com/video/${videoId}`;

                    // 1. 尝试从 RapidAPI 补充最新视频详情
                    try {
                        const vRes = await fetch(`https://${rapidHost}/api/post/detail?videoId=${videoId}`, {
                            headers: { 'x-rapidapi-host': rapidHost, 'x-rapidapi-key': rapidKey }
                        });
                        if (vRes.ok) {
                            const vData = await vRes.json();
                            const item = vData.data || (vData.itemInfo && vData.itemInfo.itemStruct) || vData;
                            if (item) {
                                videoTitle = item.desc || item.title || videoTitle;
                                const author = item.author || {};
                                videoChannel = '@' + (author.nickname || author.uniqueId || 'TikToker');
                                const vStruct = item.video || {};
                                videoCover = vStruct.cover || vStruct.dynamicCover || item.cover || videoCover;
                            }
                        }
                    } catch(err) {}

                    loadingBar.style.width = '60%';
                    // 2. 从 RapidAPI 获取视频评论列表
                    const cRes = await fetch(`https://${rapidHost}/api/post/comments?videoId=${videoId}&count=40&cursor=0`, {
                        headers: { 'x-rapidapi-host': rapidHost, 'x-rapidapi-key': rapidKey }
                    });
                    if (!cRes.ok) throw new Error(`RapidAPI 响应错误 (状态码: ${cRes.status})`);
                    const cData = await cRes.json();
                    const rawComments = (cData.data && cData.data.comments) || cData.comments || [];
                    
                    let comments = [];
                    for (let c of rawComments) {
                        const text = c.text || '';
                        if (text && text.split(' ').length > 2 && !text.includes('http')) {
                            const user = c.user || {};
                            const authorName = user.nickname || user.unique_id || "tiktok_user";
                            const avatar = (user.avatar_thumb && user.avatar_thumb.url_list && user.avatar_thumb.url_list[0]) || user.avatar_thumb || "https://p16-va.tiktokcdn.com/obj/tos-maliva-p-0068/default_avatar.jpeg";
                            const likes = parseInt(c.digg_count || 0);

                            comments.push({
                                author: authorName,
                                avatar: avatar,
                                text: text.replace(/\b[A-Z]{2,}\b/g, match => match.toLowerCase()),
                                likes: likes
                            });
                        }
                    }

                    comments.sort((a, b) => b.likes - a.likes);
                    comments = comments.slice(0, 35);

                    loadingBar.style.width = '75%';
                    const videoObj = { title: videoTitle, channel: videoChannel, thumb: videoCover, url: videoUrl, id: videoId };
                    const htmlOutput = generateBaseHTMLString(videoObj, comments, AppState.year, AppState.month, AppState.day);

                    const now = new Date();
                    const yearStr = AppState.year.toString();
                    const monthStr = AppState.month.toString();
                    const dayStr = AppState.day.toString();
                    const hhmmStr = String(now.getHours()).padStart(2, '0') + ':' + String(now.getMinutes()).padStart(2, '0');
                    const hhmmFile = String(now.getHours()).padStart(2, '0') + String(now.getMinutes()).padStart(2, '0');
                    const filename = `${yearStr}_${monthStr}_${dayStr}_${hhmmFile}_tiktok.html`;
                    const fileRelPath = `${yearStr}/${monthStr}/${filename}`;

                    loadingBar.style.width = '85%';
                    await fetch(`https://api.github.com/repos/${ghOwner}/${ghRepo}/contents/docs/${fileRelPath}`, {
                        method: 'PUT',
                        headers: { 'Authorization': `token ${ghToken}`, 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message: `Add tiktok video: ${videoTitle.substring(0, 30)}`, content: btoa(unescape(encodeURIComponent(htmlOutput))) })
                    });

                    loadingBar.style.width = '95%';
                    const idxRes = await fetch(`https://api.github.com/repos/${ghOwner}/${ghRepo}/contents/docs/index.html`, { headers: { 'Authorization': `token ${ghToken}` } });
                    const idxData = await idxRes.json();
                    const idxContent = decodeURIComponent(escape(atob(idxData.content)));
                    const dataStart = idxContent.indexOf('/*DATA_START*/') + 14;
                    const dataEnd = idxContent.indexOf('/*DATA_END*/');
                    const archiveObj = JSON.parse(idxContent.substring(dataStart, dataEnd));

                    if (!archiveObj[yearStr]) archiveObj[yearStr] = {};
                    if (!archiveObj[yearStr][monthStr]) archiveObj[yearStr][monthStr] = {};
                    if (!archiveObj[yearStr][monthStr][dayStr]) archiveObj[yearStr][monthStr][dayStr] = [];
                    
                    const newItem = { time: hhmmStr, path: fileRelPath, title: `🎵 TikTok 单集精读: ${videoTitle}` };
                    archiveObj[yearStr][monthStr][dayStr].unshift(newItem);
                    const newIdxContent = idxContent.substring(0, dataStart) + JSON.stringify(archiveObj) + idxContent.substring(dataEnd);
                    
                    await fetch(`https://api.github.com/repos/${ghOwner}/${ghRepo}/contents/docs/index.html`, {
                        method: 'PUT',
                        headers: { 'Authorization': `token ${ghToken}`, 'Content-Type': 'application/json' },
                        body: JSON.stringify({ message: `Update calendar index`, content: btoa(unescape(encodeURIComponent(newIdxContent))), sha: idxData.sha })
                    });

                    if (!archiveData[yearStr]) archiveData[yearStr] = {};
                    if (!archiveData[yearStr][monthStr]) archiveData[yearStr][monthStr] = {};
                    if (!archiveData[yearStr][monthStr][dayStr]) archiveData[yearStr][monthStr][dayStr] = [];
                    archiveData[yearStr][monthStr][dayStr].unshift(newItem);

                    forceRender(); 
                    loadingBar.style.width = '100%';
                    showToast('🎉 抓取成功并归档云端！');
                    this.value = '';
                    setTimeout(() => { loadingBar.style.width = '0%'; }, 1500);
                } catch (err) {
                    alert('❌ 操作失败: ' + err.message); 
                    loadingBar.style.width = '0%';
                } finally { this.disabled = false; }
            }
        });

        // ================= 数据驱动防污染生成器 =================
        const ENGINE_B64 = 'REPLACEME_ENGINE_B64';
        function b64DecodeUnicode(str) {
            return decodeURIComponent(atob(str).split('').map(function(c) {
                return '%' + ('00' + c.charCodeAt(0).toString(16)).slice(-2);
            }).join(''));
        }
        const engineScriptContent = b64DecodeUnicode(ENGINE_B64);

        function generateBaseHTMLString(video, comments, sYear, sMonth, sDay) {
            const pageData = {
                year: sYear, month: sMonth, day: sDay,
                video: {
                    title: video.title,
                    channel: video.channel,
                    thumb: video.thumb,
                    url: video.url,
                    now_str: `${sYear}-${String(sMonth).padStart(2,'0')}-${String(sDay).padStart(2,'0')} ${String(new Date().getHours()).padStart(2,'0')}:${String(new Date().getMinutes()).padStart(2,'0')}`
                },
                comments: comments.map(c => ({
                    author: c.author,
                    avatar: c.avatar,
                    likes_str: c.likes >= 1000 ? (c.likes / 1000).toFixed(1) + "k" : c.likes.toString(),
                    text: c.text,
                    annotation: ""
                }))
            };
            
            const pageDataStr = JSON.stringify(pageData).replace(/</g, '\\u003c');

            function escapeHTML(str) {
                if (typeof str !== 'string') return '';
                return str.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;').replace(/'/g, '&#039;');
            }

            let comments_html = "";
            pageData.comments.forEach(c => {
                comments_html += `
                <div class="chat-message">
                    <img src="${escapeHTML(c.avatar)}" class="avatar" alt="avatar" loading="lazy">
                    <div class="message-content">
                        <div class="message-header">
                            <span class="author">${escapeHTML(c.author)}</span>
                            <span class="likes">❤️ ${escapeHTML(c.likes_str)}</span>
                        </div>
                        <div class="para-wrap">
                            <div class="bubble card-text">${escapeHTML(c.text)}<span class="anno-toggle" title="点击添加/查看批注">🔴</span><span class="ai-toggle" title="AI俚语解析">🤖</span></div>
                            <div class="anno-box" style="display:none;">
                                <div class="anno-view markdown-body"></div>
                                <textarea class="anno-edit" style="display:none;" placeholder="在此记录该评论的俚语拆解或灵感..."></textarea>
                            </div>
                        </div>
                    </div>
                </div>`;
            });

            return `<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <title>${escapeHTML(pageData.video.title)}</title>
    <script src="https://cdn.jsdelivr.net/npm/marked/marked.min.js"><` + `/script>
    <style>
        :root { --bg: #f2f2f7; --card: #ffffff; --text: #1c1e21; --muted: #8e8e93; --accent: #fe2c55; --bubble: #e5e5ea; }
        body { font-family: -apple-system, BlinkMacSystemFont, "SF Pro Text", "Helvetica Neue", sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 0; text-align: left; -webkit-font-smoothing: antialiased; }
        .container { max-width: 600px; margin: 0 auto; padding: 0 0 50px 0; }
        .nav-back { padding: 15px; text-align: center; background: var(--card); position: sticky; top: 0; z-index: 100; box-shadow: 0 2px 10px rgba(0,0,0,0.05); display: flex; justify-content: center; align-items: center; }
        .nav-back a { text-decoration: none; color: white; background: #fe2c55; padding: 8px 20px; border-radius: 20px; font-weight: bold; font-size: 0.9rem; position: relative; z-index: 2;}
        .sync-status { padding: 4px 10px; border-radius: 12px; font-size: 0.75rem; font-weight: bold; display: none; color: #fff; background: #2ea44f; position: absolute; right: 15px; z-index: 3; }
        
        .video-card { background: var(--card); border-bottom-left-radius: 24px; border-bottom-right-radius: 24px; overflow: hidden; box-shadow: 0 4px 20px rgba(0,0,0,0.04); margin-bottom: 25px; }
        .video-thumb { width: 100%; max-height: 400px; display: block; object-fit: contain; background: #000; }
        .video-info { padding: 20px; }
        .v-channel { font-size: 0.85rem; color: var(--accent); font-weight: 700; margin-bottom: 6px; display: block; }
        .v-title { font-size: 1.15rem; font-weight: 700; margin: 0 0 15px 0; line-height: 1.4; display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden; }
        .v-actions { display: flex; justify-content: space-between; align-items: center; border-top: 1px solid #f0f0f0; padding-top: 15px; }
        .timestamp { font-size: 0.85rem; color: var(--muted); font-weight: 500; }
        .btn-play { background: #000; color: #fff; text-decoration: none; padding: 8px 16px; border-radius: 20px; font-size: 0.9rem; font-weight: 700; }
        
        .chat-container { padding: 0 15px; display: flex; flex-direction: column; gap: 20px; }
        .chat-message { display: flex; gap: 12px; align-items: flex-start; }
        .avatar { width: 40px; height: 40px; border-radius: 50%; object-fit: cover; background: #ddd; flex-shrink: 0; }
        .message-content { flex: 1; min-width: 0; }
        .message-header { display: flex; justify-content: space-between; align-items: flex-end; margin-bottom: 4px; padding-left: 2px; }
        .author { font-size: 0.85rem; color: var(--muted); font-weight: 600; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 70%; }
        .likes { font-size: 0.75rem; color: var(--accent); font-weight: 700; background: #ffebee; padding: 2px 8px; border-radius: 10px; }
        .empty-state { text-align: center; color: var(--muted); padding: 40px 20px; }
        
        .para-wrap { width: 100%; display: flex; flex-direction: column; align-items: flex-start; }
        .bubble { background: var(--card); padding: 12px 16px; border-radius: 2px 18px 18px 18px; font-size: 1.05rem; line-height: 1.5; color: var(--text); box-shadow: 0 2px 8px rgba(0,0,0,0.03); white-space: pre-wrap; word-wrap: break-word; }
        .anno-toggle, .ai-toggle { display: inline-block; margin-left: 8px; cursor: pointer; opacity: 0.3; font-size: 0.85rem; vertical-align: baseline; padding: 2px 4px; border-radius: 4px; transition: all 0.2s; user-select: none; }
        .anno-toggle:hover, .ai-toggle:hover { opacity: 0.8; transform: scale(1.1); }
        .anno-toggle.has-anno { opacity: 1; }
        .ai-toggle.loading::after { content: "⏳"; display: inline-block; animation: spin 1s linear infinite; }
        @keyframes spin { 100% { transform: rotate(360deg); } }
        
        .anno-box { display: none; margin-top: 8px; width: 100%; box-sizing: border-box; background: #fff; border-left: 3px solid var(--accent); padding: 12px; border-radius: 8px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }
        .anno-view { font-size: 0.95rem; line-height: 1.5; color: #333; }
        .anno-edit { width: 100%; min-height: 80px; padding: 10px; font-family: monospace; font-size: 0.95rem; border: 1px dashed #ccc; border-radius: 6px; box-sizing: border-box; display: none; resize: vertical; }
        .anno-edit:focus { outline: none; border: 1px solid var(--accent); }

        .markdown-body p { margin-top: 0; margin-bottom: 8px; }
        .markdown-body p:last-child { margin-bottom: 0; }
        .markdown-body h1, .markdown-body h2, .markdown-body h3 { color: var(--accent); font-size: 1.1rem; margin: 10px 0 8px 0; border-bottom: 1px dashed #eee; padding-bottom: 4px; }
        .markdown-body ul, .markdown-body ol { margin: 0 0 8px 0; padding-left: 20px; }
        .markdown-body blockquote { margin: 0 0 10px 0; padding: 10px 15px; background: #f9f9f9; border-left: 4px solid var(--accent); color: #666; }
    </style>
</head>
<body>
    <div class="nav-back">
        <a href="../../index.html">🔙 返回日曆樞紐</a>
        <span id="sync-status" class="sync-status"></span>
    </div>
    <div class="container">
        <h2 style="text-align: center; margin-bottom: 25px; color: #333;">📅 ${pageData.year}-${String(pageData.month).padStart(2,'0')}-${String(pageData.day).padStart(2,'0')}</h2>
        <div class="video-card">
            <a href="${escapeHTML(pageData.video.url)}" target="_blank"><img src="${escapeHTML(pageData.video.thumb)}" class="video-thumb" alt="Thumbnail"></a>
            <div class="video-info">
                <span class="v-channel">${escapeHTML(pageData.video.channel)}</span>
                <h1 class="v-title">${escapeHTML(pageData.video.title)}</h1>
                <div class="v-actions">
                    <span class="timestamp">更新於: ${escapeHTML(pageData.video.now_str)}</span>
                    <a href="${escapeHTML(pageData.video.url)}" target="_blank" class="btn-play">▶ 原片</a>
                </div>
            </div>
        </div>
        <div class="chat-container">
            ${comments_html ? comments_html : '<div class="empty-state">暫无高价值评论。</div>'}
        </div>
    </div>
    <script id="page-data" type="application/json">${pageDataStr}<` + `/script>
    <script id="matrix-engine">${engineScriptContent}<` + `/script>
</body>
</html>`;
        }
    </script>
</body>
</html>"""

    html_template = html_template.replace('REPLACEME_JSON_DATA', json_data)
    html_template = html_template.replace('REPLACEME_ENGINE_B64', engine_b64)

    os.makedirs(BASE_DIR, exist_ok=True)
    with open(os.path.join(BASE_DIR, "index.html"), "w", encoding="utf-8") as f:
        f.write(html_template)
    print("✅ `docs/index.html` 纯客户端日历枢纽已构建（加入了 AI 误触保护、下拉框修复和全局 Toast）！")

if __name__ == "__main__":
    generate_index_template()
