// Smart grade import UI and review flow.
// This module intentionally keeps OCR files in the browser until upload; the service is configured separately.
S.gradeImport = {
  serviceUrl: 'http://localhost:8080',
  processing: false,
  rows: [],
  documents: [],
  roster: [],
  groupId: '',
  subjectId: '',
  status: '',
  errors: []
};
try {
  const savedImportUrl = localStorage.getItem('tmv_grade_import_url');
  if (savedImportUrl) S.gradeImport.serviceUrl = savedImportUrl;
} catch (e) {}

function gradeImportSelection() {
  if (S.role === 'teacher') {
    const select = $('gi_assign');
    const selected = select ? select.value : (S.ta.key || '');
    const a = S.assigns.find(x => akey(x) === selected) || S.assigns[0] || null;
    return a ? {schoolId:a.a_school, groupId:a.a_group, subjectId:a.a_subject} : null;
  }
  const groupId = $('gi_group') ? $('gi_group').value : (S.gradeImport.groupId || S.gid || (S.groups[0] && S.groups[0].id));
  const group = S.groups.find(g => g.id === groupId);
  const subjects = S.subjects.filter(s => !group || s.levels.includes(group.level));
  const subjectId = $('gi_subject') ? $('gi_subject').value : (S.gradeImport.subjectId || (subjects[0] && subjects[0].id));
  return S.school && groupId && subjectId ? {schoolId:S.school.id, groupId, subjectId} : null;
}

function gradeImportRoster(groupId) {
  if (S.role === 'teacher') return (S.gradeImport.roster || []).map(s => ({
    id:s.r_student || s.student_id || s.id,
    no:s.r_no || s.list_number,
    first:s.r_first || s.first_name,
    last:s.r_last || s.last_name
  }));
  return S.students.filter(s => s.group_id === groupId && s.status === 'مسجل')
    .map(s => ({id:s.id, no:s.list_number, first:s.first_name, last:s.last_name}));
}

function gradeImportHtml() {
  const a = S.gradeImport;
  let choose = '';
  if (S.role === 'teacher') {
    if (!S.assigns.length) {
      choose = '<div class="warnbox">لا توجد تعيينات نشطة. اطلب من المدير تعيينك على مادة وفوج أولًا.</div>';
    } else {
      const selectedAssignment = S.assigns.find(x => akey(x) === S.ta.key) || S.assigns[0];
      const active = akey(selectedAssignment);
      choose = '<label class="f">المادة والفوج المسند<select id="gi_assign" onchange="S.ta.key=this.value;S.gradeImport.rows=[];S.gradeImport.documents=[];render()">';
      S.assigns.forEach(x => {
        choose += '<option value="' + esc(akey(x)) + '"' + (akey(x) === active ? ' selected' : '') + '>' +
          esc(x.a_subject_name) + ' — ' + esc(x.a_group_name) + '</option>';
      });
      choose += '</select></label>';
    }
  } else {
    const groupId = a.groupId || S.gid || (S.groups[0] && S.groups[0].id) || '';
    const group = S.groups.find(g => g.id === groupId);
    const subjects = S.subjects.filter(s => !group || s.levels.includes(group.level));
    const subjectId = a.subjectId || (subjects[0] && subjects[0].id) || '';
    choose = '<label class="f">الفوج<select id="gi_group" onchange="S.gradeImport.groupId=this.value;S.gradeImport.subjectId=\'\';S.gradeImport.rows=[];render()">';
    S.groups.forEach(g => {
      choose += '<option value="' + esc(g.id) + '"' + (g.id === groupId ? ' selected' : '') + '>' + esc(g.name) + '</option>';
    });
    choose += '</select></label><label class="f">المادة<select id="gi_subject" onchange="S.gradeImport.subjectId=this.value">';
    subjects.forEach(s => {
      choose += '<option value="' + esc(s.id) + '"' + (s.id === subjectId ? ' selected' : '') + '>' + esc(s.name) + '</option>';
    });
    choose += '</select></label>';
  }

  let summaries = '';
  a.documents.forEach(d => {
    const c = d.classification || {};
    summaries += '<div class="notice"><span><b>' + esc(d.filename) + '</b> — ' +
      (c.is_grade_sheet ? 'مؤشرات كشف نقاط' : 'لم يُؤكد أنه كشف نقاط') +
      ' · ثقة ' + Math.round((c.confidence || 0) * 100) + '%<br>' +
      esc((c.reasons || []).join(' ')) + '</span></div>';
  });
  const status = a.status ? '<div class="notice">' + esc(a.status) + '</div>' : '';
  const errors = a.errors.length ? '<div class="warnbox">' + a.errors.map(esc).join('<br>') + '</div>' : '';
  const rows = a.rows.length ? gradeImportRowsHtml() :
    '<div class="empty"><b>لم تُحلل ملفات بعد</b>ارفع كشف نقاط أو التقط صورة له، ثم ابدأ التحليل.</div>';
  const requiresDocumentConfirmation = a.documents.some(d =>
    !d.classification || !d.classification.is_grade_sheet || d.classification.review_required
  );
  const documentConfirmation = requiresDocumentConfirmation && a.rows.length
    ? '<div class="warnbox"><label class="row"><input id="gi_confirm_document" class="gi-check" type="checkbox">أؤكد بعد مراجعة المستند أنه كشف علامات صالح للاستيراد، رغم انخفاض مؤشرات التصنيف.</label></div>'
    : '';

  return '<div class="card"><h3>' + ic('file') + 'استيراد العلامات من صورة أو ملف</h3>' +
    '<p class="note">ارفع صورًا من الهاتف أو ملفات PDF وExcel وWord. تُعتمد حاليًا الحقول الأربعة الموجودة في نظامك: التقويم المستمر والفرض الأول والثاني والاختبار؛ أما المعدلات والأعمدة الإضافية فلا تُحفظ كحقول مستقلة. لن تُحفظ أي علامة قبل اعتمادك.</p>' +
    '<div class="gi-controls">' + choose +
    '<label class="f">عنوان خدمة المعالجة (API)<input id="gi_service" type="url" value="' + esc(a.serviceUrl) +
    '" placeholder="http://localhost:8080" onchange="setGradeImportUrl(this.value)"></label></div>' +
    '<div class="gi-drop" style="margin-top:14px"><div class="gi-controls">' +
    '<label class="f">اختيار ملفات متعددة<input id="gi_files" type="file" multiple accept=".png,.jpg,.jpeg,.webp,.bmp,.tif,.tiff,.pdf,.xlsx,.xls,.docx,image/*,application/pdf"></label>' +
    '<label class="f">التقاط صورة بالكاميرا<input id="gi_camera" type="file" accept="image/*" capture="environment"></label></div>' +
    '<p class="note">الصيغ المدعومة: JPG/PNG/WebP/TIFF وPDF وXLS/XLSX وDOCX. حد الخدمة الافتراضي 15 ميغابايت للملف الواحد.</p>' +
    '<button class="btn" style="margin-top:8px" onclick="previewGradeImport()" ' + (a.processing ? 'disabled' : '') + '>' +
    (a.processing ? 'جارٍ تحليل الملفات…' : 'تحليل الملفات وعرض المعاينة') + '</button></div>' + status + errors + summaries + '</div>' +
    '<div class="card"><div class="row sb"><h3>' + ic('edit') + 'مراجعة العلامات المستخرجة</h3><div class="row">' +
    '<span class="tag">' + a.rows.length + ' صف</span><button class="btn gold sm" onclick="commitGradeImport()" ' +
    (a.processing || !a.rows.length ? 'disabled' : '') + '>اعتماد وحفظ المحدد</button></div></div>' +
    (a.rows.length ? '<div class="notice">اختر التلميذ لكل صف، راجع الأرقام ومصدرها، ثم علّم الصفوف التي توافق على حفظها. لن يتم الحفظ تلقائيًا. قد يتم حفظ الصفوف السابقة إذا تعذر حفظ صف لاحق لأن النظام الحالي يحفظ كل حقل باستدعاء مستقل.</div>' : '') +
    documentConfirmation + '<div class="gi-preview">' + rows + '</div></div>';
}

function setGradeImportUrl(value) {
  const url = String(value || '').trim().replace(/\/+$/, '');
  if (!/^https?:\/\/[^/]+/i.test(url)) return say('أدخل عنوان خدمة صحيحًا يبدأ بـ http:// أو https://.', false);
  S.gradeImport.serviceUrl = url;
  try { localStorage.setItem('tmv_grade_import_url', url); } catch (e) {}
  say('تم حفظ عنوان خدمة المعالجة على هذا الجهاز.');
}

function gradeImportRowsHtml() {
  const rows = S.gradeImport.rows;
  const selection = gradeImportSelection();
  const groupId = S.gradeImport.groupId || (selection && selection.groupId);
  const roster = gradeImportRoster(groupId);
  let out = '<div class="tw"><table><tr><th>اعتماد</th><th>المصدر</th><th>الاسم المستخرج</th><th>الرقم</th><th>مطابقة التلميذ</th>';
  CC.forEach(k => { out += '<th>' + CCN[k] + '</th>'; });
  out += '<th>الثقة</th><th>التحذير</th></tr>';
  rows.forEach((r, i) => {
    const status = r.matchStatus === 'exact_name_and_number' || r.matchStatus === 'exact_name' ? 'مطابقة حرفية مقترحة' :
      (r.matchStatus === 'duplicate_in_import' ? 'مكرر في الملفات' : (r.matchStatus === 'conflict' ? 'تعارض في الاسم/الرقم' : 'تأكيد يدوي'));
    const source = [r.source && r.source.file, r.source && (r.source.location || ('صفحة ' + (r.source.page || '—'))),
      r.source && (r.source.cell || ('سطر ' + (r.source.row || r.source.line || '—')))].filter(Boolean).join(' · ');
    out += '<tr class="' + (r.studentId ? '' : 'gi-row-low') + '">' +
      '<td><input class="gi-check" type="checkbox" ' + (r.selected ? 'checked' : '') +
      ' onchange="giSelectRow(' + i + ',this.checked)" aria-label="اعتماد هذا الصف"></td>' +
      '<td><div class="gi-source">' + esc(source) + '</div></td>' +
      '<td><b>' + esc(r.rawName || '—') + '</b></td><td>' + (r.listNumber == null ? '—' : esc(r.listNumber)) + '</td>' +
      '<td><select style="min-width:200px" onchange="giSetStudent(' + i + ',this.value)"><option value="">اختر التلميذ…</option>';
    roster.forEach(s => {
      out += '<option value="' + esc(s.id) + '"' + (s.id === r.studentId ? ' selected' : '') + '>' +
        esc(s.no) + ' — ' + esc(s.first) + ' ' + esc(s.last) + '</option>';
    });
    out += '</select><div class="gi-status ' + (r.studentId ? 'ok' : 'warn') + '">' + status + '</div></td>';
    CC.forEach(k => {
      const fieldSource = r.sources && r.sources[k] ? r.sources[k] : {};
      const cellSource = fieldSource.cell || (fieldSource.page ? ('صفحة ' + fieldSource.page + '، سطر ' + (fieldSource.line || '—')) : '');
      out += '<td><input class="gi-number" type="number" min="0" max="20" step="0.01" value="' +
        (r.grades[k] == null ? '' : esc(r.grades[k])) + '" onchange="giSetGrade(' + i + ',\'' + k + '\',this.value)" aria-label="' + CCN[k] + '">' +
        '<div class="gi-source">' + esc(cellSource) + '</div></td>';
    });
    out += '<td><span class="gi-confidence">' + Math.round((r.confidence || 0) * 100) + '%</span></td>' +
      '<td><span class="gi-source">' + esc([...(r.matchReasons || []), ...(r.warnings || [])].join(' ')) + '</span></td></tr>';
  });
  return out + '</table></div>';
}

function giSelectRow(index, checked) {
  const row = S.gradeImport.rows[index];
  if (row) row.selected = !!checked;
}
function giSetStudent(index, studentId) {
  const row = S.gradeImport.rows[index];
  if (row) { row.studentId = studentId || ''; if (studentId) row.selected = true; }
}
function giSetGrade(index, field, value) {
  const row = S.gradeImport.rows[index];
  if (!row) return;
  if (String(value).trim() === '') { row.grades[field] = null; return; }
  const number = Number(String(value).replace(',', '.'));
  row.grades[field] = Number.isFinite(number) ? number : value;
}

async function previewGradeImport() {
  const selection = gradeImportSelection();
  if (!selection) return say('اختر المؤسسة والفوج والمادة أو تعيين الأستاذ أولًا.', false);
  const base = String(S.gradeImport.serviceUrl || ($('gi_service') && $('gi_service').value) || '').trim().replace(/\/+$/, '');
  if (!/^https?:\/\/[^/]+/i.test(base)) return say('أدخل عنوان خدمة المعالجة أولًا.', false);
  const chosen = Array.from(($('gi_files') && $('gi_files').files) || []);
  const camera = $('gi_camera') && $('gi_camera').files && $('gi_camera').files[0];
  if (camera) chosen.push(camera);
  if (!chosen.length) return say('اختر ملفًا أو التقط صورة أولًا.', false);
  if (chosen.length > 8) return say('الحد الأقصى 8 ملفات في الطلب.', false);

  S.gradeImport.serviceUrl = base;
  S.gradeImport.processing = true;
  S.gradeImport.errors = [];
  S.gradeImport.status = 'يجري التحقق من الجلسة وتحليل المستندات…';
  render();
  try {
    const session = await sb.auth.getSession();
    if (session.error || !session.data || !session.data.session || !session.data.session.access_token)
      throw new Error('انتهت الجلسة؛ سجّل الدخول من جديد.');
    const body = new FormData();
    body.append('school_id', selection.schoolId);
    body.append('group_id', selection.groupId);
    body.append('subject_id', selection.subjectId);
    chosen.forEach(f => body.append('files', f, f.name));
    const response = await fetch(base + '/api/v1/grade-import/preview', {
      method: 'POST',
      headers: {Authorization:'Bearer ' + session.data.session.access_token},
      body
    });
    let result = {};
    try { result = await response.json(); } catch (e) {}
    if (!response.ok) throw new Error(result.detail || 'فشل تحليل الملفات (' + response.status + ').');

    S.gradeImport.documents = result.documents || [];
    S.gradeImport.groupId = selection.groupId;
    S.gradeImport.subjectId = selection.subjectId;
    S.gradeImport.roster = result.students || [];
    S.gradeImport.rows = (result.rows || []).map(r => ({
      rawName:r.raw_name || '',
      listNumber:r.list_number == null ? null : r.list_number,
      grades:{
        cc:r.grades && r.grades.cc != null ? r.grades.cc : null,
        f1:r.grades && r.grades.f1 != null ? r.grades.f1 : null,
        f2:r.grades && r.grades.f2 != null ? r.grades.f2 : null,
        ex:r.grades && r.grades.ex != null ? r.grades.ex : null
      },
      sources:r.sources || {},
      confidence:Number(r.confidence || 0),
      source:r.source || {},
      warnings:r.warnings || [],
      matchReasons:r.match_reasons || [],
      matchStatus:r.match_status || 'unmatched',
      studentId:r.suggested_student_id || '',
      selected:false
    }));
    if (S.role === 'teacher') {
      S.ta.key = selection.groupId + '|' + selection.subjectId;
      S.ta.roster = S.gradeImport.roster.map(s => ({
        r_student:s.r_student || s.student_id,
        r_no:s.r_no || s.list_number,
        r_first:s.r_first || s.first_name,
        r_last:s.r_last || s.last_name
      }));
      const old = await sb.rpc('teacher_group_grades', {p_group:selection.groupId, p_subject:selection.subjectId});
      if (old.error) throw new Error('تعذر قراءة العلامات الحالية للمقارنة: ' + old.error.message);
      S.ta.grades = {};
      rowsOf(old.data).forEach(x => {
        S.ta.grades[x.g_student] = {cc:n(x.g_cc), f1:n(x.g_f1), f2:n(x.g_f2), ex:n(x.g_ex), note:x.g_note || ''};
      });
    }
    S.gradeImport.status = 'اكتمل التحليل: ' + (result.document_count || 0) +
      ' مستند، ' + (result.row_count || 0) + ' صف. لم تُحفظ أي علامة.';
    if (!S.gradeImport.rows.length) S.gradeImport.errors.push('لم تُستخرج صفوف علامات قابلة للمراجعة. جرّب ملف Excel منظمًا أو صورة أوضح.');
  } catch (error) {
    S.gradeImport.status = '';
    S.gradeImport.errors = [error.message || String(error)];
  } finally {
    S.gradeImport.processing = false;
    render();
  }
}

async function commitGradeImport() {
  const rows = S.gradeImport.rows.filter(r => r.selected);
  if (!rows.length) return say('حدد صفًا واحدًا على الأقل للموافقة على حفظه.', false);
  const uncertainDocument = S.gradeImport.documents.some(d =>
    !d.classification || !d.classification.is_grade_sheet || d.classification.review_required
  );
  if (uncertainDocument && !($('gi_confirm_document') && $('gi_confirm_document').checked))
    return say('راجع تصنيف المستند وأكد يدويًا أنه كشف علامات قبل الحفظ.', false);
  const selection = gradeImportSelection() || {
    schoolId:S.school && S.school.id,
    groupId:S.gradeImport.groupId,
    subjectId:S.gradeImport.subjectId
  };
  if (!selection || !selection.groupId || !selection.subjectId) return say('تعذر تحديد الفوج أو المادة؛ أعد اختيار التعيين.', false);
  const roster = gradeImportRoster(selection.groupId);
  const ids = new Set();
  let overwriteCount = 0;

  for (const row of rows) {
    if (!row.studentId || !roster.some(s => s.id === row.studentId))
      return say('يوجد صف بلا تلميذ صحيح. اختر التلميذ يدويًا أولًا.', false);
    if (ids.has(row.studentId)) return say('يوجد تلميذ محدد أكثر من مرة. عالج التكرار قبل الحفظ.', false);
    ids.add(row.studentId);
    if (!CC.some(k => row.grades[k] !== null && row.grades[k] !== '' && row.grades[k] !== undefined))
      return say('يوجد صف بلا علامات قابلة للحفظ.', false);
    for (const k of CC) {
      const value = row.grades[k];
      if (value === null || value === '' || value === undefined) continue;
      if (!Number.isFinite(Number(value)) || Number(value) < 0 || Number(value) > 20)
        return say('توجد علامة غير صالحة؛ يجب أن تكون بين 0 و20.', false);
      const old = S.role === 'teacher'
        ? (S.ta.grades[row.studentId] && S.ta.grades[row.studentId][k])
        : (S.G[row.studentId] && S.G[row.studentId][selection.subjectId] && S.G[row.studentId][selection.subjectId][k]);
      if (old != null && Number(old) !== Number(value)) overwriteCount++;
    }
  }

  const warning = overwriteCount ? '\nتنبيه: ' + overwriteCount + ' حقلًا يحتوي علامة سابقة وسيتم استبدالها.' : '';
  if (!confirm('سيتم اعتماد ' + rows.length + ' صفًا للمادة المختارة.\nسيُستخدم الحفظ الحالي لكل حقل ويُسجّل في سجل التدقيق.' +
    warning + '\nهل توافق؟')) return;

  S.gradeImport.processing = true;
  S.gradeImport.status = 'يجري حفظ العلامات المعتمدة…';
  S.gradeImport.errors = [];
  render();
  let saved = 0;
  const failed = [];
  try {
    for (const row of rows) {
      let rowFailed = false;
      for (const field of CC) {
        const value = row.grades[field];
        if (value === null || value === '' || value === undefined) continue;
        const result = await sb.rpc('save_grade_field', {
          p_student:row.studentId, p_subject:selection.subjectId,
          p_field:field, p_value:Number(value)
        });
        if (result.error) {
          failed.push((row.rawName || row.studentId) + ' — ' + CCN[field] + ': ' + result.error.message);
          rowFailed = true;
          break;
        }
        const savedRow = rowsOf(result.data)[0] || {};
        if (S.role === 'teacher') {
          S.ta.grades[row.studentId] = {
            cc:n(savedRow.g_cc), f1:n(savedRow.g_f1), f2:n(savedRow.g_f2), ex:n(savedRow.g_ex),
            note:savedRow.g_note || ''
          };
        } else {
          applyGradeRow(row.studentId, selection.subjectId, savedRow);
        }
      }
      if (!rowFailed) { saved++; row.selected = false; }
    }
    S.gradeImport.status = 'تم حفظ ' + saved + ' صفًا بنجاح.' + (failed.length ? ' تعذر حفظ بعض الصفوف.' : '');
    S.gradeImport.errors = failed;
    if (saved) S._dirty = true;
  } finally {
    S.gradeImport.processing = false;
    render();
  }
}
