/* ================================================================
   инструктаж.js — логика страницы электронного инструктажа
   ================================================================ */

/* ─── Глобальные переменные ───────────────────────────────────── */
let currentCategory = null;
let currentCategoryBtn = null;
let selectedIncidentPath = "";
let ME = null; // {login, role, scope_company_id, scope_orgunit_id}

/* ─── Вспомогательные функции ─────────────────────────────────── */
const MONTH_RU = {
  '01':'Январь','02':'Февраль','03':'Март','04':'Апрель','05':'Май','06':'Июнь',
  '07':'Июль','08':'Август','09':'Сентябрь','10':'Октябрь','11':'Ноябрь','12':'Декабрь'
};
const fmtYM = d => `${d.getUTCFullYear()}-${String(d.getUTCMonth()+1).padStart(2,'0')}`;
const ymToPretty = ym => { const [y,m] = ym.split('-'); return `${MONTH_RU[m]||m} ${y}`; };

function token() { return localStorage.getItem('instr_token'); }
function authedFetch(url, init) { return fetch(url, init); }

async function loadMe() {
  const r = await authedFetch('/api/dashboard/me');
  if (!r.ok) throw new Error('Не удалось получить данные пользователя');
  const m = await r.json();
  if (!m.idnum && m.scope_company_id) m.idnum = m.scope_company_id;
  return m;
}

function isMobile() {
  return /Android|webOS|iPhone|iPad|iPod|BlackBerry|IEMobile|Opera Mini/i.test(navigator.userAgent)
    || window.innerWidth <= 768;
}

/* ─── Инициализация при загрузке ──────────────────────────────── */
document.addEventListener('DOMContentLoaded', async () => {
  // Загружаем данные пользователя
  try {
    ME = await loadMe();
  } catch(e) {
    console.error('Ошибка загрузки данных пользователя:', e);
    ME = null;
  }

  // Шапка: показываем имя + кнопку выхода
  const cont = document.querySelector('.header-login-container');
  if (cont) {
    cont.innerHTML = '';
    let displayName = '—';
    if (ME && ME.login) {
      const [tabNum, orgRaw] = ME.login.split('@');
      const orgFormatted = orgRaw ? orgRaw.toUpperCase().replace(/(\D+)(\d+)/, '$1-$2') : '';
      if (ME.fio && ME.fio.trim()) {
        const parts = ME.fio.trim().split(/\s+/);
        displayName = `${parts.slice(0,2).join(' ')} ${orgFormatted}`.trim();
      } else {
        displayName = orgFormatted ? `${tabNum} ${orgFormatted}` : tabNum;
      }
    }

    const who = document.createElement('div');
    who.textContent = displayName;
    who.style.fontWeight = '600';
    cont.appendChild(who);

    const outBtn = document.createElement('button');
    outBtn.className = 'header-btn logout-btn';
    outBtn.type = 'button';
    outBtn.textContent = 'Выйти';
    outBtn.addEventListener('click', () => window.logout && window.logout());
    cont.appendChild(outBtn);
  }

  // Скрываем кнопку скачать в модалке
  const dl = document.getElementById('downloadLink');
  if (dl) dl.style.display = 'none';

  // Слушатель выбора месяца через скрытый input
  const mp = document.getElementById('monthPicker');
  const pretty = document.getElementById('monthPretty');
  mp.addEventListener('change', () => {
    if (currentCategory && mp.value) {
      pretty.textContent = ymToPretty(mp.value);
      localStorage.setItem('i2_month', mp.value);
      loadFiles(currentCategory, mp.value);
    }
  });

  // Кнопка показа/скрытия пройденных
  const showDoneBtn = document.getElementById('showDoneBtn');
  if (showDoneBtn) {
    const getFlag = () => JSON.parse(localStorage.getItem('i2_show_done') || 'false');
    const setFlag = v => localStorage.setItem('i2_show_done', JSON.stringify(v));
    const syncBtn = () => {
      const on = getFlag();
      showDoneBtn.textContent = on ? 'Скрыть пройденные' : 'Показать пройденные';
      showDoneBtn.style.background = on ? '#111' : '#5b9bd5';
      showDoneBtn.style.borderColor  = on ? '#111' : '#5b9bd5';
    };
    if (localStorage.getItem('i2_show_done') === null) setFlag(true);
    syncBtn();
    showDoneBtn.addEventListener('click', () => {
      setFlag(!getFlag());
      syncBtn();
      if (currentCategory && mp.value) loadFiles(currentCategory, mp.value);
    });
  }
});

/* ─── Выбор категории инструктажа ─────────────────────────────── */
function loadCategory(category, btn) {
  currentCategory = category;
  const wasActive = btn.classList.contains('active-button');
  document.querySelectorAll('.category-btn').forEach(b => b.classList.remove('active-button'));
  const welcomeMsg = document.getElementById('welcomeMessage');

  if (wasActive) {
    currentCategory = null;
    if (welcomeMsg) welcomeMsg.style.display = 'block';
    document.getElementById('simpleForm').style.display = 'none';
    document.getElementById('monthToolbar').style.display = 'none';
    return;
  }

  btn.classList.add('active-button');
  if (welcomeMsg) welcomeMsg.style.display = 'none';

  if (category === 'vvodny' || category === 'pervichny') {
    document.getElementById('monthToolbar').style.display = 'none';
    document.getElementById('filesContainer').innerHTML = '';
    showSimpleForm(category);
    return;
  }

  document.getElementById('simpleForm').style.display = 'none';
  document.getElementById('viewer').classList.remove('open');

  const toolbar   = document.getElementById('monthToolbar');
  const filesWrap = document.getElementById('filesContainer');
  const mp        = document.getElementById('monthPicker');
  const pretty    = document.getElementById('monthPretty');

  toolbar.style.display = 'flex';
  filesWrap.innerHTML = '';

  function setMonthAndLoad(d) {
    const ym = fmtYM(d);
    mp.value = ym;
    pretty.textContent = ymToPretty(ym);
    localStorage.setItem('i2_month', ym);
    loadFiles(category, ym);
  }

  document.getElementById('prevMonthBtn').onclick = () => {
    if (!mp.value) return setMonthAndLoad(new Date());
    const [y, m] = mp.value.split('-').map(Number);
    setMonthAndLoad(new Date(Date.UTC(y, m-2, 1)));
  };
  document.getElementById('nextMonthBtn').onclick = () => {
    if (!mp.value) return setMonthAndLoad(new Date());
    const [y, m] = mp.value.split('-').map(Number);
    setMonthAndLoad(new Date(Date.UTC(y, m, 1)));
  };
  document.getElementById('thisMonthBtn').onclick = () => setMonthAndLoad(new Date());

  // Свайп по названию месяца
  let tx = null;
  pretty.addEventListener('touchstart', e => { tx = e.touches[0].clientX; }, { passive: true });
  pretty.addEventListener('touchend', e => {
    if (tx == null) return;
    const dx = e.changedTouches[0].clientX - tx; tx = null;
    if (Math.abs(dx) < 40) return;
    if (dx < 0) document.getElementById('nextMonthBtn').click();
    else        document.getElementById('prevMonthBtn').click();
  }, { passive: true });

  const saved = localStorage.getItem('i2_month');
  setMonthAndLoad(saved ? new Date(saved + '-01T00:00:00Z') : new Date());
}

/* ─── Форма вводного/первичного инструктажа ────────────────────── */
async function showSimpleForm(typeKey) {
  document.getElementById('simpleForm').style.display = 'grid';
  document.getElementById('monthToolbar').style.display = 'none';
  document.getElementById('filesContainer').innerHTML = '';

  document.getElementById('simpleTitle').textContent =
    typeKey === 'vvodny' ? 'Вводный инструктаж' : 'Первичный инструктаж';
  document.getElementById('simpleDate').value = new Date().toLocaleString('ru-RU');
  ['simpleId','simpleFio','simpleYear','simpleProf','simpleDept','simpleInstrName']
    .forEach(id => document.getElementById(id).value = '');

  const preview = document.getElementById('simplePreview');
  preview.innerHTML = '';
  delete preview.dataset.path;

  let files = [];
  try {
    const r = await fetch(`/api/dashboard/files?type=${encodeURIComponent(typeKey)}&month=`);
    files = await r.json();
  } catch(e) { console.error(e); }

  if (!Array.isArray(files) || !files.length) {
    preview.innerHTML = '<p>Инструктажи не найдены.</p>';
    return;
  }

  const fname   = typeof files[0] === 'string' ? files[0] : files[0].name;
  const path    = `instruktagi/${typeKey}/${fname}`;
  const ext     = fname.split('.').pop().toLowerCase();
  const fileUrl = `${window.location.origin}/${path}`;
  preview.dataset.path = path;

  if (ext === 'pdf') {
    preview.innerHTML = `<iframe src="${fileUrl}" style="width:100%;height:70vh;border:none"></iframe>`;
  } else if (['jpg','jpeg','png','gif','webp','bmp','svg'].includes(ext)) {
    preview.innerHTML = `<img src="${fileUrl}" style="max-width:100%;display:block;margin:auto">`;
  } else if (['mp4','webm','avi','mov','mkv','wmv','flv','m4v'].includes(ext)) {
    const mimes = { mp4:'video/mp4', webm:'video/webm', avi:'video/x-msvideo',
                    mov:'video/quicktime', mkv:'video/x-matroska', wmv:'video/x-ms-wmv',
                    flv:'video/x-flv', m4v:'video/mp4' };
    preview.innerHTML = `<video controls preload="metadata" style="width:100%;max-height:70vh;background:#000">
      <source src="${fileUrl}" type="${mimes[ext]||'video/mp4'}">
      <p style="padding:10px">Видео не поддерживается. <a href="${fileUrl}" download>Скачать</a></p>
    </video>`;
  } else if (ext === 'docx' || ext === 'doc') {
    preview.innerHTML = `<p>Документ: <a href="${fileUrl}" target="_blank" rel="noopener">Открыть ${fname}</a></p>`;
  } else {
    preview.innerHTML = `<p>Формат не поддерживается: ${ext}. <a href="${fileUrl}" target="_blank">Открыть файл</a></p>`;
  }
}

function cancelSimple() {
  document.getElementById('simpleForm').style.display = 'none';
  document.getElementById('monthToolbar').style.display = 'none';
  document.getElementById('filesContainer').innerHTML = '';
}

async function submitSimple() {
  const typeKey = currentCategory;
  const data = {
    type: typeKey,
    timestamp: Date.now(),
    idnum:          document.getElementById('simpleId').value.trim(),
    fio:            document.getElementById('simpleFio').value.trim(),
    birthday:       document.getElementById('simpleYear').value,
    profession:     document.getElementById('simpleProf').value.trim(),
    cex:            document.getElementById('simpleDept').value.trim(),
    incident:       document.getElementById('simplePreview').dataset.path || '',
    signature:      document.getElementById('simpleCanvasEmp').toDataURL(),
    instrSignature: document.getElementById('simpleCanvasInstr').toDataURL(),
    instructorName: document.getElementById('simpleInstrName').value.trim(),
    description:    ''
  };
  const resp = await authedFetch('/api/records', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(data)
  });
  if (resp && resp.status === 409) {
    alert('Этот инструктаж уже был сохранён ранее.');
  } else {
    alert('Инструктаж сохранён');
  }
  cancelSimple();
}

/* ─── Загрузка списка файлов ──────────────────────────────────── */
async function loadFiles(category, ym) {
  const filesWrap = document.getElementById('filesContainer');
  filesWrap.innerHTML = '<div style="color:#6b7280;padding:12px">Загрузка…</div>';

  try {
    const listResp = await authedFetch(
      `/api/dashboard/files?type=${encodeURIComponent(category)}&month=${encodeURIComponent(ym)}`
    );
    if (!listResp.ok) throw new Error(listResp.status);
    const list = await listResp.json();

    if (!Array.isArray(list) || !list.length) {
      filesWrap.innerHTML = '<div style="color:#6b7280;padding:12px">В этом месяце нет файлов.</div>';
      return;
    }

    // Пройденные файлы — запрашиваем только свои записи для этого типа/месяца
    let completedFiles = new Set();
    try {
      const myResp = await authedFetch(
        `/api/records/my?type=${encodeURIComponent(category)}&month=${encodeURIComponent(ym)}`
      );
      if (myResp.ok) {
        const myRecs = await myResp.json();
        completedFiles = new Set(myRecs.map(r => r.file));
      }
    } catch(e) { console.warn('Не удалось загрузить записи:', e); }

    try { list.sort((a,b) => (a.mtime||0) - (b.mtime||0)); } catch(_) {}

    filesWrap.innerHTML = '';
    const showDone = JSON.parse(localStorage.getItem('i2_show_done') || 'false');

    list.forEach(f => {
      const name        = typeof f === 'string' ? f : f.name;
      const path        = `instruktagi/${category}/${ym}/${name}`;
      const isCompleted = completedFiles.has(name);
      if (!showDone && isCompleted) return;

      const card = document.createElement('div');
      card.className    = 'file-card';
      card.dataset.path = path;
      card.innerHTML = `
        <div class="title">${name}</div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
          <button class="header-btn" type="button" data-action="open">Открыть</button>
          ${isCompleted ? '<span class="badge-done">✓ Пройдено</span>' : ''}
        </div>`;
      if (isCompleted) card.classList.add('completed');
      card.querySelector('[data-action="open"]').onclick = () => openModal(path);
      filesWrap.appendChild(card);
    });
  } catch(e) {
    console.error('Ошибка загрузки:', e);
    filesWrap.innerHTML = `<div style="color:#d61b3c;padding:12px">Ошибка загрузки: ${e?.message || 'неизвестная ошибка'}</div>`;
  }
}

/* ─── Модальное окно просмотра ─────────────────────────────────── */
async function showPreview(path) {
  const viewer = document.getElementById('viewer');
  const pr     = document.getElementById('preview');

  pr.innerHTML = '';
  viewer.classList.add('open');
  document.body.dataset.sy  = String(window.scrollY || 0);
  document.body.style.overflow = 'hidden';

  // Сброс quiz
  document.getElementById('confirm').checked = false;
  document.getElementById('quizBlock').style.display  = 'none';
  document.getElementById('quizQuestions').innerHTML  = '';
  document.getElementById('quizResult').style.display = 'none';
  document.getElementById('submitQuiz').style.display = 'none';
  document.getElementById('signatureBlock').style.display = 'none';
  quizPassed = false;
  currentQuizQuestions = [];

  const fname   = decodeURIComponent(path.split('/').pop());
  const ext     = fname.split('.').pop().toLowerCase();
  const fileUrl = `${window.location.origin}/${path}`;
  const mobile  = isMobile();

  document.getElementById('viewerTitle').textContent = fname;
  document.getElementById('downloadLink').href = `/${path}`;

  // PDF
  if (ext === 'pdf') {
    if (mobile) {
      // Android Chrome не рендерит PDF в iframe — используем Google Docs Viewer
      const encodedUrl = encodeURIComponent(fileUrl);
      pr.innerHTML = `
        <div style="width:100%;height:100%;flex:1;display:flex;flex-direction:column;position:relative">
          <div id="pdf-mobile-loader"
               style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:#f8fafc;z-index:2">
            <div style="text-align:center;color:#64748b">
              <div style="font-size:32px;margin-bottom:8px">📄</div>
              <div>Загружаем документ…</div>
              <div style="font-size:12px;margin-top:4px;color:#94a3b8">Через Google Docs Viewer</div>
            </div>
          </div>
          <iframe src="https://docs.google.com/viewer?url=${encodedUrl}&embedded=true"
                  style="flex:1;width:100%;border:none;"
                  allowfullscreen
                  onload="var l=document.getElementById('pdf-mobile-loader');if(l)l.style.display='none'">
          </iframe>
        </div>`;
    } else {
      pr.innerHTML = `<iframe src="${fileUrl}" style="width:100%;height:100%;flex:1;border:none;" allowfullscreen></iframe>`;
    }
    selectedIncidentPath = path;
    return;
  }

  // Изображения
  if (['jpg','jpeg','png','gif','webp','bmp','svg'].includes(ext)) {
    pr.innerHTML = `<img src="${fileUrl}" style="max-width:100%;display:block;margin:auto;padding:12px">`;
    selectedIncidentPath = path;
    return;
  }

  // Видео
  if (['mp4','webm','avi','mov','mkv','wmv','flv','m4v'].includes(ext)) {
    const mimes = { mp4:'video/mp4', webm:'video/webm', avi:'video/x-msvideo',
                    mov:'video/quicktime', mkv:'video/x-matroska', wmv:'video/x-ms-wmv',
                    flv:'video/x-flv', m4v:'video/mp4' };
    pr.innerHTML = `
      <video controls preload="metadata" playsinline webkit-playsinline
             style="width:100%;flex:1;max-height:100%;background:#000;object-fit:contain">
        <source src="${fileUrl}" type="${mimes[ext] || 'video/mp4'}">
        <p style="color:#fff;padding:20px">Ваш браузер не поддерживает видео.
          <a href="${fileUrl}" download style="color:#4a9eff">Скачать файл</a></p>
      </video>`;
    selectedIncidentPath = path;
    return;
  }

  // DOCX — client-side rendering via docx-preview (both mobile & desktop)
  if (ext === 'docx') {
    pr.innerHTML = '<div class="docx-container" id="docx-container"><div style="padding:24px;text-align:center;color:#64748b"><div style="font-size:32px;margin-bottom:8px">📄</div>Загружаем документ…</div></div>';
    selectedIncidentPath = path;

    fetch(fileUrl)
      .then(r => { if (!r.ok) throw new Error('Не удалось загрузить файл'); return r.arrayBuffer(); })
      .then(buf => {
        const container = document.getElementById('docx-container');
        if (!container) return;
        if (typeof docx === 'undefined' || !docx.renderAsync) {
          container.innerHTML = `<div style="padding:24px;text-align:center">
            <p style="color:#dc2626;margin-bottom:16px">⚠️ Библиотека просмотра не загружена</p>
            <a href="${fileUrl}" download class="header-btn" style="display:inline-block;text-decoration:none">⬇️ Скачать файл</a>
          </div>`;
          return;
        }
        docx.renderAsync(buf, container, null, {
          className:'docx', inWrapper:true, ignoreWidth:false, ignoreHeight:false,
          renderHeaders:true, renderFooters:true, useBase64URL:false
        }).catch(() => {
          container.innerHTML = `<div style="padding:24px;text-align:center">
            <p style="color:#dc2626;margin-bottom:16px">❌ Ошибка отображения документа</p>
            <a href="${fileUrl}" download class="header-btn" style="display:inline-block;text-decoration:none">⬇️ Скачать файл</a>
          </div>`;
        });
      })
      .catch(() => {
        const c = document.getElementById('docx-container');
        if (c) c.innerHTML = `<div style="padding:24px;text-align:center">
          <p style="color:#dc2626;margin-bottom:16px">❌ Не удалось загрузить документ</p>
          <a href="${fileUrl}" download class="header-btn" style="display:inline-block;text-decoration:none">⬇️ Скачать файл</a>
        </div>`;
      });
    return;
  }

  // DOC (legacy) — Google Docs Viewer on mobile, native on desktop
  if (ext === 'doc') {
    if (mobile) {
      const encodedUrl = encodeURIComponent(fileUrl);
      pr.innerHTML = `
        <div style="width:100%;height:100%;flex:1;display:flex;flex-direction:column;position:relative">
          <div id="docx-mobile-loader"
               style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;background:#f8fafc;z-index:2">
            <div style="text-align:center;color:#64748b">
              <div style="font-size:32px;margin-bottom:8px">📄</div>
              <div>Загружаем документ…</div>
              <div style="font-size:12px;margin-top:4px;color:#94a3b8">Через Google Docs Viewer</div>
            </div>
          </div>
          <iframe src="https://docs.google.com/viewer?url=${encodedUrl}&embedded=true"
                  style="flex:1;width:100%;border:none;"
                  allowfullscreen
                  onload="var l=document.getElementById('docx-mobile-loader');if(l)l.style.display='none'">
          </iframe>
        </div>`;
    } else {
      pr.innerHTML = `<iframe src="${fileUrl}" style="width:100%;height:100%;flex:1;border:none;" allowfullscreen></iframe>`;
    }
    selectedIncidentPath = path;
    return;
  }

  pr.innerHTML = `<p style="padding:16px">Формат не поддерживается.
    <a href="${fileUrl}" target="_blank" rel="noopener">Открыть/скачать</a></p>`;
  selectedIncidentPath = path;
}

function openModal(path) { showPreview(path); }

function closeViewer() {
  const viewer = document.getElementById('viewer');
  viewer.classList.remove('open');
  document.body.style.overflow = '';
  window.scrollTo(0, +(document.body.dataset.sy || 0));
  document.getElementById('preview').innerHTML = '';
  document.getElementById('confirm').checked   = false;
  document.getElementById('signatureBlock').style.display = 'none';
  document.getElementById('quizBlock').style.display      = 'none';
  document.getElementById('quizQuestions').innerHTML      = '';
  document.getElementById('quizResult').style.display     = 'none';
  document.getElementById('submitQuiz').style.display     = 'none';
  quizPassed = false;
  currentQuizQuestions = [];
}

// Закрытие по Esc / клик на оверлей / свайп вниз
document.getElementById('closeViewer').onclick = closeViewer;
document.getElementById('viewer').addEventListener('click', e => { if (e.target.id === 'viewer') closeViewer(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape') closeViewer(); });
/* Свайп вниз закрывает модалку — только по шапке, чтобы не мешать скроллу */
(function() {
  const header = document.querySelector('.viewer-header');
  if (!header) return;
  let sy = null;
  header.addEventListener('touchstart', e => { sy = e.touches[0].clientY; }, { passive: true });
  header.addEventListener('touchend',   e => {
    if (sy == null) return;
    const dy = e.changedTouches[0].clientY - sy; sy = null;
    if (dy > 80) closeViewer();
  }, { passive: true });
})();

/* ─── Квиз (проверка знаний) ──────────────────────────────────── */
let quizPassed = false;
let currentQuizLanguage = 'ru';
let currentQuizQuestions = [];

document.addEventListener('change', e => {
  if (e.target.id === 'confirm') {
    if (e.target.checked) {
      document.getElementById('quizBlock').style.display    = 'block';
      document.getElementById('signatureBlock').style.display = 'none';
      const fs = document.getElementById('footerScrollable');
      if (fs) fs.scrollTop = 0;
      loadQuizQuestions();
    } else {
      document.getElementById('quizBlock').style.display    = 'none';
      document.getElementById('signatureBlock').style.display = 'none';
      quizPassed = false;
    }
  }
  if (e.target.name === 'quizLang') {
    currentQuizLanguage = e.target.value;
    loadQuizQuestions();
  }
});

async function loadQuizQuestions() {
  if (!selectedIncidentPath) { alert('Сначала откройте файл инструктажа'); return; }

  const quizLoading   = document.getElementById('quizLoading');
  const quizQuestions = document.getElementById('quizQuestions');
  const submitQuizBtn = document.getElementById('submitQuiz');
  const quizResult    = document.getElementById('quizResult');

  quizLoading.style.display   = 'block';
  quizLoading.innerHTML       = 'Загрузка вопросов...';
  quizQuestions.style.display = 'none';
  quizQuestions.innerHTML     = '';
  submitQuizBtn.style.display = 'none';
  quizResult.style.display    = 'none';

  try {
    const resp = await fetch('/api/quiz/get-questions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: selectedIncidentPath, language: currentQuizLanguage })
    });

    const text = await resp.text();
    if (!resp.ok) {
      let errMsg = `Ошибка ${resp.status}`;
      try { errMsg = JSON.parse(text).detail || errMsg; } catch { errMsg = text.substring(0, 200) || errMsg; }
      throw new Error(errMsg);
    }

    const data = JSON.parse(text);
    currentQuizQuestions = data.questions;

    quizLoading.style.display   = 'none';
    quizQuestions.style.display = 'block';
    submitQuizBtn.style.display = 'block';
    renderQuizQuestions(data.questions);

    const fs = document.getElementById('footerScrollable');
    if (fs) fs.scrollTop = 0;
  } catch(err) {
    console.error('Quiz error:', err);
    quizLoading.innerHTML = `<div style="color:#dc2626;padding:12px">Ошибка: ${err.message}</div>`;
  }
}

function renderQuizQuestions(questions) {
  const container = document.getElementById('quizQuestions');
  container.innerHTML = '';

  questions.forEach((q, idx) => {
    const qDiv = document.createElement('div');
    qDiv.style.cssText = 'margin-bottom:16px;padding:12px;background:#fff;border:1px solid #e0e6f0;border-radius:8px';

    const qTitle = document.createElement('div');
    qTitle.style.cssText = 'font-weight:600;margin-bottom:10px;color:#1f365c;font-size:15px';
    qTitle.textContent = `${idx + 1}. ${q.question}`;
    qDiv.appendChild(qTitle);

    const optionsDiv = document.createElement('div');
    optionsDiv.style.cssText = 'display:flex;flex-direction:column;gap:6px';

    const opts = q.options || q.choices || {};

    const buildLabel = (value, text) => {
      const label = document.createElement('label');
      label.style.cssText = 'display:flex;align-items:flex-start;gap:10px;cursor:pointer;padding:8px 10px;border-radius:6px;transition:background 0.15s;border:1px solid transparent';
      label.onmouseover = () => label.style.background = '#f1f5f9';
      label.onmouseout  = () => label.style.background = 'transparent';
      const radio = document.createElement('input');
      radio.type  = 'radio';
      radio.name  = `q${idx}`;
      radio.value = value;
      radio.style.cssText = 'margin-top:3px;accent-color:#5b9bd5;flex-shrink:0';
      const span = document.createElement('span');
      span.textContent = text;
      label.appendChild(radio);
      label.appendChild(span);
      return label;
    };

    if (Array.isArray(opts)) {
      opts.forEach((optText, optIdx) => {
        const match = optText.match(/^([A-D])\)/);
        const value = match ? match[1] : String.fromCharCode(65 + optIdx);
        optionsDiv.appendChild(buildLabel(value, optText));
      });
    } else {
      ['A','B','C','D'].forEach(letter => {
        const option = opts[letter];
        if (!option) return;
        optionsDiv.appendChild(buildLabel(letter, `${letter}. ${option}`));
      });
    }

    qDiv.appendChild(optionsDiv);
    container.appendChild(qDiv);
  });
}

document.getElementById('submitQuiz').onclick = async function() {
  const answers = {};
  currentQuizQuestions.forEach((q, idx) => {
    const sel = document.querySelector(`input[name="q${idx}"]:checked`);
    if (sel) answers[String(idx)] = sel.value;
  });

  if (Object.keys(answers).length < currentQuizQuestions.length) {
    alert('Ответьте на все вопросы');
    return;
  }

  try {
    const resp = await fetch('/api/quiz/submit', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file_path: selectedIncidentPath, language: currentQuizLanguage, answers })
    });
    if (!resp.ok) { const err = await resp.json(); throw new Error(err.detail || 'Ошибка проверки ответов'); }
    const result = await resp.json();
    showQuizResult(result);
    if (result.passed) {
      quizPassed = true;
      setTimeout(() => {
        document.getElementById('signatureBlock').style.display = 'block';
        const fs = document.getElementById('footerScrollable');
        if (fs) fs.scrollTop = fs.scrollHeight;
      }, 2000);
    }
  } catch(err) {
    console.error('Submit quiz error:', err);
    alert('Ошибка: ' + err.message);
  }
};

function showQuizResult(result) {
  const resultDiv = document.getElementById('quizResult');
  resultDiv.style.display = 'block';
  const fs = document.getElementById('footerScrollable');
  if (fs) setTimeout(() => { fs.scrollTop = 0; }, 50);

  const percent = Math.round(result.score_percentage);
  const passed  = result.passed;
  resultDiv.style.background = passed ? '#dcfce7' : '#fee2e2';
  resultDiv.style.border     = `1px solid ${passed ? '#86efac' : '#fca5a5'}`;
  resultDiv.style.color      = passed ? '#166534' : '#991b1b';
  resultDiv.style.borderRadius = '8px';
  resultDiv.style.padding    = '12px 16px';

  let html = `<div style="font-weight:700;margin-bottom:6px;font-size:16px">
    ${passed ? '✅ Тест пройден!' : '❌ Тест не пройден'}
  </div>`;
  html += `<div>Правильных ответов: ${result.correct_count} из ${result.total_count} (${percent}%)</div>`;
  if (!passed) {
    html += `<div style="margin-top:6px">Необходимо минимум 75%. Попробуйте ещё раз.</div>
             <button onclick="retryQuiz()" class="header-btn" style="margin-top:10px">Пройти заново</button>`;
  }
  resultDiv.innerHTML = html;
}

window.retryQuiz = function() {
  quizPassed = false;
  document.getElementById('quizResult').style.display = 'none';
  document.querySelectorAll('input[type="radio"]').forEach(r => r.checked = false);
};

/* ─── Канвас для подписи (модалка) ────────────────────────────── */
const canvas = document.getElementById('canvas');
const ctx    = canvas.getContext('2d');
let drawing  = false;

/* Подгоняем внутренний размер canvas под CSS-размер, чтобы
   координаты не смещались на мобильных устройствах */
function resizeCanvas() {
  const rect = canvas.getBoundingClientRect();
  const w = Math.round(rect.width)  || 500;
  const h = Math.round(rect.height) || 150;
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width  = w;
    canvas.height = h;
  }
}

function getXY(ev, rect) {
  const scaleX = canvas.width  / rect.width;
  const scaleY = canvas.height / rect.height;
  const clientX = ev.touches ? ev.touches[0].clientX : ev.clientX;
  const clientY = ev.touches ? ev.touches[0].clientY : ev.clientY;
  return { x: (clientX - rect.left) * scaleX, y: (clientY - rect.top) * scaleY };
}

canvas.addEventListener('pointerdown', e => {
  e.preventDefault();
  resizeCanvas();
  const r = canvas.getBoundingClientRect();
  const {x, y} = getXY(e, r);
  drawing = true;
  ctx.lineWidth = 2;
  ctx.lineCap = 'round';
  ctx.lineJoin = 'round';
  ctx.strokeStyle = '#000';
  ctx.beginPath();
  ctx.moveTo(x, y);
}, { passive: false });
canvas.addEventListener('pointermove', e => {
  if (!drawing) return;
  e.preventDefault();
  const r = canvas.getBoundingClientRect();
  const {x, y} = getXY(e, r);
  ctx.lineTo(x, y);
  ctx.stroke();
}, { passive: false });
['pointerup','pointercancel','pointerleave'].forEach(ev => canvas.addEventListener(ev, () => drawing = false));

function clearCanvas() { ctx.clearRect(0, 0, canvas.width, canvas.height); }
window.clearCanvas = clearCanvas;

function hasNonTrivialSignature(cnv) {
  const imgData = cnv.getContext('2d').getImageData(0, 0, cnv.width, cnv.height).data;
  let colored = 0;
  for (let i = 3; i < imgData.length; i += 4) {
    if (imgData[i] > 0 && ++colored > 200) return true;
  }
  return false;
}

/* ─── Отправка записи об инструктаже ──────────────────────────── */
async function submitRecord() {
  try {
    if (!selectedIncidentPath) { alert('Сначала откройте инструктаж.'); return; }
    if (!document.getElementById('confirm').checked) {
      alert('Отметьте чекбокс «Ознакомлен с инструктажом».'); return;
    }
    if (!hasNonTrivialSignature(canvas)) {
      alert('Пожалуйста, подпишите поле. Подпись слишком простая/пустая.'); return;
    }

    const me    = ME || await loadMe();
    const login = me?.login;
    if (!login)              { alert('Нет логина пользователя – авторизуйтесь заново.'); return; }
    if (!login.includes('@')){ alert('Неверный формат логина. Ожидается: табельный@организация'); return; }

    const idnum = login.split('@')[0];
    if (!idnum.trim()) { alert('Табельный номер не может быть пустым'); return; }

    let fio = ME?.fio || login;
    if (!ME?.fio) {
      try {
        const meResp = await authedFetch('/api/dashboard/me');
        if (meResp.ok) {
          const meData = await meResp.json();
          if (meData.fio) fio = meData.fio;
        }
      } catch(e) { console.warn('Не удалось получить ФИО:', e); }
    }

    const resp = await authedFetch('/api/records', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        idnum, fio,
        type: currentCategory,
        incident: selectedIncidentPath,
        signature: canvas.toDataURL(),
        instrSignature: null
      })
    });

    if (resp.status === 409) {
      alert('Вы уже прошли этот инструктаж. Повторное прохождение не требуется.');
      return;
    }
    if (!resp.ok) {
      const txt = await resp.text().catch(() => '');
      throw new Error(txt || ('HTTP ' + resp.status));
    }

    // Визуально помечаем карточку
    document.querySelectorAll('.file-card').forEach(card => {
      if (card.dataset.path === selectedIncidentPath) {
        card.classList.add('completed');
        const badge = document.createElement('span');
        badge.className = 'badge-done';
        badge.textContent = 'Пройдено';
        card.appendChild(badge);
        const btn = card.querySelector('button[data-action="open"]');
        if (btn) { btn.disabled = true; btn.textContent = 'Просмотрен'; }
      }
    });

    alert('Инструктаж сохранён.');
  } catch(e) {
    console.error('Ошибка отправки записи', e);
    alert('Ошибка: не удалось сохранить результат. ' + (e?.message || ''));
    return;
  } finally {
    document.getElementById('confirm').checked = false;
    document.getElementById('signatureBlock').style.display = 'none';
    clearCanvas();
    closeViewer();
  }
}
window.submitForm = submitRecord;

/* ─── Канвасы простой формы ───────────────────────────────────── */
function initSimpleCanvas(id) {
  const c   = document.getElementById(id);
  const ctx = c.getContext('2d');
  let drawing = false;
  c.style.touchAction = 'none';

  function getXY(e) {
    const rect   = c.getBoundingClientRect();
    const clientX = e.touches ? e.touches[0].clientX : e.clientX;
    const clientY = e.touches ? e.touches[0].clientY : e.clientY;
    return [(clientX - rect.left) * (c.width / rect.width),
            (clientY - rect.top)  * (c.height / rect.height)];
  }

  c.addEventListener('pointerdown', e => { e.preventDefault(); drawing = true; const [x,y] = getXY(e); ctx.beginPath(); ctx.moveTo(x,y); }, { passive: false });
  c.addEventListener('pointermove', e => { if (!drawing) return; e.preventDefault(); const [x,y] = getXY(e); ctx.lineTo(x,y); ctx.stroke(); }, { passive: false });
  ['pointerup','pointerleave','pointercancel'].forEach(ev => c.addEventListener(ev, () => drawing = false));
}
initSimpleCanvas('simpleCanvasEmp');
initSimpleCanvas('simpleCanvasInstr');

function clearSimpleEmp()   { const c = document.getElementById('simpleCanvasEmp');   c.getContext('2d').clearRect(0,0,c.width,c.height); }
function clearSimpleInstr() { const c = document.getElementById('simpleCanvasInstr'); c.getContext('2d').clearRect(0,0,c.width,c.height); }
window.clearSimpleEmp   = clearSimpleEmp;
window.clearSimpleInstr = clearSimpleInstr;

/* ─── Экспорт в Excel ─────────────────────────────────────────── */
document.getElementById('exportSimpleBtn').addEventListener('click', exportSimple);

async function exportSimple() {
  const typeLabel = currentCategory === 'vvodny' ? 'Вводный' : 'Первичный';
  // Фильтруем по текущему месяцу (из pickera или текущий)
  const mp = document.getElementById('monthPicker');
  const ym = (mp && mp.value) || (function(){ const d=new Date(); return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,'0')}`; })();
  const qs = new URLSearchParams({ type: currentCategory, month: ym });
  const all  = await authedFetch('/api/records?' + qs.toString()).then(r => r.json()).catch(() => []);
  const rows = all.filter(r => r.type === currentCategory && String(r.month||'').slice(0,7) === ym);
  const pad2 = n => String(n).padStart(2, '0');

  const wb  = XLSX.utils.book_new();
  const aoa = [];

  if (currentCategory === 'pervichny') {
    aoa.push(['Дата','ФИО','Год рождения','Профессия','Вид','Причина','ФИО инструктора','Подпись инструктора','Подпись работника']);
    rows.forEach(rec => {
      const d  = new Date(rec.timestamp);
      const dt = `${pad2(d.getDate())}.${pad2(d.getMonth()+1)}.${d.getFullYear()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
      aoa.push([dt, rec.fio, rec.birthday||'', rec.profession||'', 'Первичный инструктаж', '',
        rec.instructorName||'',
        rec.instrSignatureLink ? {t:'s',v:'✍️ Посмотреть подпись',l:{Target:rec.instrSignatureLink}} : '',
        rec.signatureLink      ? {t:'s',v:'✍️ Посмотреть подпись',l:{Target:rec.signatureLink}}      : ''
      ]);
    });
  } else {
    aoa.push(['Дата и время','ИИН','ФИО','Год рождения','Профессия, должность','Место работы','Подпись работника','Подпись инструктора','ФИО инструктора']);
    rows.forEach(rec => {
      const d  = new Date(rec.timestamp);
      const dt = `${pad2(d.getDate())}.${pad2(d.getMonth()+1)}.${d.getFullYear()} ${pad2(d.getHours())}:${pad2(d.getMinutes())}`;
      const r  = [dt, rec.idnum||'', rec.fio||'', rec.birthday||'', rec.profession||'', rec.cex||''];
      r.push(rec.signatureLink ? {t:'s',v:'✍️ Посмотреть подпись',l:{Target:rec.signatureLink}} : '');
      let instr = rec.instrSignatureLink || '';
      if (instr.startsWith('file:///')) { const i = instr.indexOf('/signatures/'); if (i !== -1) instr = instr.substring(i); }
      r.push(instr ? {t:'s',v:'✍️ Посмотреть подпись',l:{Target:instr}} : '');
      r.push(rec.instructorName || '');
      aoa.push(r);
    });
  }

  const ws = XLSX.utils.aoa_to_sheet(aoa);
  ws['!cols'] = aoa[0].map(h => ({ wch: Math.max(12, String(h).length + 2) }));
  XLSX.utils.book_append_sheet(wb, ws, typeLabel.slice(0, 31));
  XLSX.writeFile(wb, `${typeLabel}.xlsx`);
}

/* ─── Совместимые хелперы ─────────────────────────────────────── */
function needLogin() {
  const to = encodeURIComponent(decodeURIComponent(location.pathname.split('/').pop() || 'инструктаж.html'));
  location.href = './login.html?to=' + to;
}
function loadFilesHelper(type, month) {
  return authedFetch(`/api/dashboard/files?type=${encodeURIComponent(type)}&month=${encodeURIComponent(month)}`);
}
function openModalHelper(path) { openModal(path); }
function initSignPad() { /* инициализировано pointer-событиями выше */ }
async function submitRecordHelper() { return submitRecord(); }
