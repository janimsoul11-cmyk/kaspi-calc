/**
 * Threads: контент-план + уведомления в Telegram.
 *
 * Один файл заменяет весь старый код проекта.
 * Порядок запуска описан в threads/README.md, коротко:
 *   1) вписать TG_TOKEN и TG_CHAT_ID ниже
 *   2) меню «Threads-бот» → «Перезалить контент-план»
 *   3) меню «Threads-бот» → «Переустановить триггеры»  (лечит дубли уведомлений)
 */

// ==================== НАСТРОЙКИ ====================

const TG_TOKEN   = 'ВСТАВЬТЕ_ТОКЕН_БОТА';   // из старого кода / у @BotFather
const TG_CHAT_ID = 'ВСТАВЬТЕ_CHAT_ID';      // ваш chat_id (число, у @userinfobot)

const TZ          = 'Asia/Almaty';  // Астана
const PRE_MINUTES = 20;             // за сколько минут до слота присылать пост
const STALE_HOURS = 6;              // старше — не присылаем вообще (после простоя скрипта)
const MORNING_H   = 8;              // час утреннего плана на день

// Колонки листа (1 = A)
const C_ID = 1, C_DATE = 2, C_TIME = 3, C_RUBRIC = 4, C_TEXT = 5,
      C_LEN = 6, C_STATUS = 7, C_NOTE = 8, C_SENT = 9;
const HEADERS = ['ID','Дата','Время','Рубрика','Текст','Символов','Статус','Заметка','Отправлено в TG'];
const FIRST_ROW = 2;

// ==================== КОНТЕНТ-ПЛАН ====================

const POSTS = [
  {id: 1, date: "2026-09-11", time: "09:30", rubric: "Китай и платежи", text: "Перевёл поставщику 12 400 $. До фабрики дошло 11 990 $.\n\n410 долларов растворились между Астаной и Гуанчжоу. Агент — 2%. Курс в платёжке на 1,2% хуже биржевого. Комиссия сверху.\n\nПеред этим мы неделю торговались за 0,4 $ на единице. Партия 600 штук — выбили 240 $.\n\nВыгрызли 240 и молча отдали 410.\n\nЭкономия начинается не в переписке с фабрикой. Она начинается в платёжке.", note: "Подставь свои реальные цифры последнего перевода"},

  {id: 2, date: "2026-09-11", time: "20:30", rubric: "Скорость денег", text: "Две компании с одинаковой маржой 22%. Одна зарабатывает втрое больше.\n\nДело не в товаре и не в рекламе. В том, за сколько дней тенге уходит в закуп и возвращается на счёт.\n\nУ меня было 150 дней: предоплата, производство, карго, склад, выплаты Kaspi. Это 2,4 оборота в год.\n\nУжал до 95 дней — стало 3,8. Тот же товар, та же маржа, те же деньги. Прибыль в полтора раза выше.\n\nМы годами шлифуем процент. Зарабатывает скорость.", note: ""},

  {id: 3, date: "2026-09-12", time: "09:30", rubric: "Переговоры", text: "Фабрика скинула 9% после одной фразы. Слова «дорого» в ней не было.\n\nРаньше я писал: can you give better price? Отвечали: 3%, final.\n\nТеперь спрашиваю: на каком объёме начинается следующая ценовая ступень?\n\nСкидку просят все, и на всех у менеджера один заготовленный ответ. А ступени лежат в его прайсе. Про них не врут — их просто называют.\n\nВышло 9%. Вопрос стоил дешевле, чем месяц переписки.", note: ""},

  {id: 4, date: "2026-09-12", time: "20:30", rubric: "Кассовый разрыв", text: "В марте у меня было 11 млн прибыли в отчёте и 380 тысяч на счету.\n\nДва вечера искал, где украли. Никто не крал.\n\nПрибыль лежала на складе диванами, в предоплате фабрике и в выплатах Kaspi, которые ещё не пришли.\n\nПрибыль — это мнение. Деньги — это факт.\n\nС того месяца веду два отчёта раздельно. ОПиУ отвечает, зарабатываю ли я вообще. ДДС отвечает, доживу ли до пятницы.", note: "Проверь суммы под свой месяц"},

  {id: 5, date: "2026-09-13", time: "09:30", rubric: "Цифры без прикрас", text: "Ваша прибыль посчитана по цене партии, которой на складе уже нет.\n\nВзяли в мае по 43 000 ₸. Юань подрос, карго подорожало — новая партия выходит 47 500.\n\nПродаёте старую по 61 000, видите маржу 18 000 и радуетесь.\n\nЧтобы поставить на её место такую же, вы отдадите 47 500. Настоящая маржа — 13 500.\n\n4 500 ₸ с единицы вы не заработали. Вы их проели.\n\nМаржа считается по цене следующей закупки, а не прошлой.", note: ""},

  {id: 6, date: "2026-09-13", time: "20:30", rubric: "Механика Kaspi", text: "Один возврат съедает прибыль с трёх продаж. Считаю вслух.\n\nДиван, чек 130 000. Чистыми с него 21 000.\n\nВозврат: доставка туда 5 000, обратно 5 000, переупаковка 2 000, уценка после сборки-разборки 15 000. Минус 27 000.\n\nПродали три — заработали 63 000. Один вернулся — осталось 36 000.\n\nВ крупногабарите процент возвратов дороже процента комиссии. И его почти никогда нет в расчёте.", note: ""},

  {id: 7, date: "2026-09-14", time: "09:30", rubric: "Мнение", text: "Ниша не решает. Решает, по какой цене вы финансируете закуп.\n\nВзяли оборотку под 4% в месяц. Деньги оборачиваются за 100 дней — значит, за один оборот отдали 13%.\n\nМаржа 20% стала 7%.\n\nТот же товар у соседа без кредита даёт 20%. Ниша одна. Разница — в стоимости денег.\n\nПрежде чем искать прибыльную категорию, посчитайте, сколько стоит ваш собственный тенге.", note: ""},

  {id: 8, date: "2026-09-14", time: "20:30", rubric: "Механика Kaspi", text: "0-0-12 — это не подарок покупателю. Это кредит, который платите вы.\n\nЧеловек забирает диван за 130 000 без переплаты. Красиво.\n\nЗа эту красоту Kaspi забирает с вас до 5% чека. 6 500 ₸ с продажи.\n\nПри чистых 21 000 это треть прибыли, отданная за слово «рассрочка» в карточке.\n\nЯ не говорю отключать. Я говорю, что это статья расходов, а не бесплатный маркетинг. И у неё должна быть отдельная строка в вашем расчёте.", note: ""},

  {id: 9, date: "2026-09-15", time: "09:30", rubric: "Операционка", text: "Менеджер поднял выручку на 18%. Прибыль упала на 34%.\n\nОн не воровал и не ленился. У него был процент с оборота — он честно делал оборот.\n\nСкидка 3 000, чтобы закрыть сделку сегодня. Ещё одна. И ещё.\n\nДля него это минус 60 ₸ из премии. Для меня — минус 3 000 из прибыли.\n\nМы платим за один показатель и удивляемся, что получаем ровно его.\n\nТеперь база — валовая прибыль сделки. Скидки внезапно стали обсуждаемыми.", note: ""},

  {id: 10, date: "2026-09-15", time: "20:30", rubric: "Личная история", text: "Самая дорогая ошибка стоила мне не денег, а карточки.\n\nНовый поставщик дал цену на 8% ниже рынка. Сезон поджимал, образец я не заказал — сэкономил три недели.\n\nПришло 400 штук. Каждое двадцатое с дефектом ткани, видно только когда собрали у клиента.\n\nВозвраты, отзывы, рейтинг вниз. Карточку, которую растил семь месяцев, в топ я так и не вернул.\n\n8% на закупе стоили позиции. Образец стоил бы 40 $.", note: ""},

  {id: 11, date: "2026-09-16", time: "09:30", rubric: "Механика Kaspi", text: "Скинул 1 500 ₸ и не поднялся ни на одну позицию.\n\nМы верим, что выдача — это сортировка по цене. Тогда побеждал бы самый отчаянный.\n\nПод капотом: наличие, скорость обработки, процент выкупа, отзывы, полнота карточки, история продаж.\n\nЦена — один вход из шести. И самый дорогой лично для вас.\n\nПока не закрыты остальные пять, снижение цены — это не борьба за позицию. Это перевод своей маржи в чужой карман.", note: ""},

  {id: 12, date: "2026-09-16", time: "20:30", rubric: "Цифры без прикрас", text: "Спрашиваю селлера, сколько ему нужно продать, чтобы выйти в ноль. Пауза.\n\nЭто не «продать по себестоимости». Это отбить то, что списывается каждый месяц независимо от продаж: аренда, зарплаты, реклама, подписки, бухгалтер.\n\n600 000 ₸ постоянных, 20 000 ₸ чистыми со сделки — нужно 30 продаж. Двадцать девять — это минус.\n\nЭту цифру узнают двумя способами: считают за десять минут или встречают в конце месяца.", note: ""},

  {id: 13, date: "2026-09-17", time: "09:30", rubric: "Мнение", text: "Прошли курс и не начали. Дело не в лени.\n\nКурс объясняет, как продавать. И не отвечает на три вопроса, из-за которых люди стоят:\n\nсколько я заработаю с одной единицы,\nна какие деньги куплю вторую партию до выплат Kaspi,\nчто будет, когда часть вернут.\n\nНи один не лечится вторым курсом. Все три закрываются одной таблицей на один лист.\n\nВам не знаний не хватило. Вам не дали посчитать.", note: ""},

  {id: 14, date: "2026-09-17", time: "20:30", rubric: "Оффер", text: "Три недели пишу здесь про чужие деньги. Пора про свои.\n\nЧто я делаю платно.\n\nРазбор одного товара, час: эффективная комиссия, Red, возвраты, брак, цена замещения. На выходе маржа, в которую можно поверить.\n\nАудит магазина: матрица, маржа по SKU, ценообразование, что резать и что масштабировать. Письменным отчётом.\n\nШаблон учёта: ДДС, ОПиУ, себестоимость под Kaspi. С инструкцией, чтобы вели вы, а не я.\n\nРоста не обещаю. Обещаю цифры, которые выдержат проверку.\n\nwa.me/77789066873", note: "Проверь номер WhatsApp перед публикацией"}
];


// ==================== МЕНЮ ====================

function onOpen() {
  SpreadsheetApp.getUi()
    .createMenu('Threads-бот')
    .addItem('Перезалить контент-план', 'fillSheet')
    .addSeparator()
    .addItem('Переустановить триггеры (убрать дубли)', 'resetTriggers')
    .addItem('Диагностика', 'diagnose')
    .addItem('Тестовое сообщение в Telegram', 'sendTest')
    .addSeparator()
    .addItem('Прислать следующий пост сейчас', 'sendNextNow')
    .addItem('Сбросить отметки «Отправлено»', 'resetSentMarks')
    .addToUi();
}

// ==================== ЗАПОЛНЕНИЕ ТАБЛИЦЫ ====================

/** Полностью перезаписывает лист контент-планом из POSTS. */
function fillSheet() {
  const sh = sheet_();
  sh.clear();
  sh.clearNotes();
  if (sh.getMaxColumns() < HEADERS.length) sh.insertColumnsAfter(sh.getMaxColumns(), HEADERS.length - sh.getMaxColumns());

  sh.getRange(1, 1, 1, HEADERS.length).setValues([HEADERS])
    .setFontWeight('bold').setBackground('#1f2430').setFontColor('#ffffff')
    .setVerticalAlignment('middle');

  const values = POSTS.map(p => [p.id, p.date, p.time, p.rubric, p.text, p.text.length, 'согласовано', p.note || '', '']);
  const rng = sh.getRange(FIRST_ROW, 1, values.length, HEADERS.length);
  // Дату и время держим текстом, чтобы Sheets не превращал их в свой формат
  sh.getRange(FIRST_ROW, C_DATE, values.length, 2).setNumberFormat('@');
  rng.setValues(values);

  rng.setVerticalAlignment('top');
  sh.getRange(FIRST_ROW, C_TEXT, values.length, 1).setWrap(true);
  sh.getRange(FIRST_ROW, C_NOTE, values.length, 1).setWrap(true);
  sh.setColumnWidth(C_ID, 45);
  sh.setColumnWidth(C_DATE, 95);
  sh.setColumnWidth(C_TIME, 65);
  sh.setColumnWidth(C_RUBRIC, 150);
  sh.setColumnWidth(C_TEXT, 620);
  sh.setColumnWidth(C_LEN, 85);
  sh.setColumnWidth(C_STATUS, 120);
  sh.setColumnWidth(C_NOTE, 220);
  sh.setColumnWidth(C_SENT, 150);
  sh.setFrozenRows(1);

  const rule = SpreadsheetApp.newDataValidation()
    .requireValueInList(['черновик', 'согласовано', 'опубликован'], true).build();
  sh.getRange(FIRST_ROW, C_STATUS, values.length, 1).setDataValidation(rule);

  // Красным подсветим посты длиннее лимита Threads (500 символов)
  const lenRange = sh.getRange(FIRST_ROW, C_LEN, values.length, 1);
  const over = SpreadsheetApp.newConditionalFormatRule()
    .whenNumberGreaterThan(500).setBackground('#f4c7c3').setRanges([lenRange]).build();
  sh.setConditionalFormatRules([over]);

  // Снимаем старые отметки об отправке — план новый
  clearSentProps_();
  SpreadsheetApp.flush();
  toast_('Контент-план перезалит: ' + values.length + ' постов');
}

// ==================== УВЕДОМЛЕНИЯ ====================

/**
 * Основной обработчик (триггер раз в 5 минут).
 * Дубли исключены тремя способами:
 *   1) блокировка LockService — два запуска не идут параллельно;
 *   2) отметка времени в колонке «Отправлено в TG» — строка уходит один раз;
 *   3) дублирующий флаг в свойствах скрипта на случай сбоя записи в лист.
 */
function watch() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(10000)) return;   // предыдущий запуск ещё работает — молча выходим
  try {
    const sh = sheet_();
    const last = sh.getLastRow();
    if (last < FIRST_ROW) return;

    const data = sh.getRange(FIRST_ROW, 1, last - FIRST_ROW + 1, HEADERS.length).getValues();
    const now = new Date();
    const nowStr    = fmt_(now);
    const untilStr  = fmt_(new Date(now.getTime() + PRE_MINUTES * 60000));
    const sinceStr  = fmt_(new Date(now.getTime() - STALE_HOURS * 3600000));
    const props = PropertiesService.getScriptProperties();

    for (let i = 0; i < data.length; i++) {
      const row = data[i];
      const id = row[C_ID - 1];
      if (!id || !row[C_TEXT - 1]) continue;
      if (String(row[C_STATUS - 1]).trim() !== 'согласовано') continue;
      if (String(row[C_SENT - 1]).trim() !== '') continue;             // уже отправлен
      if (props.getProperty('sent_' + id) === '1') continue;           // страховка

      const when = dstr_(row[C_DATE - 1]) + ' ' + tstr_(row[C_TIME - 1]);
      if (when.length !== 16) continue;                                 // битая дата/время
      if (when > untilStr) continue;                                    // ещё рано
      if (when < sinceStr) { mark_(sh, i, 'пропущен (просрочен)', id); continue; }

      const ok = tg_(postMessage_(row, when, nowStr));
      if (ok) mark_(sh, i, new Date(), id);
    }
  } finally {
    lock.releaseLock();
  }
}

/** Утренний план на день — один раз в сутки. */
function morningPlan() {
  const props = PropertiesService.getScriptProperties();
  const today = Utilities.formatDate(new Date(), TZ, 'yyyy-MM-dd');
  if (props.getProperty('plan_' + today) === '1') return;   // уже отправляли сегодня

  const sh = sheet_();
  const last = sh.getLastRow();
  if (last < FIRST_ROW) return;
  const data = sh.getRange(FIRST_ROW, 1, last - FIRST_ROW + 1, HEADERS.length).getValues();

  const lines = data
    .filter(r => r[C_ID - 1] && dstr_(r[C_DATE - 1]) === today && String(r[C_STATUS - 1]).trim() === 'согласовано')
    .map(r => '• ' + tstr_(r[C_TIME - 1]) + ' — ' + esc_(String(r[C_RUBRIC - 1])) + ' (#' + r[C_ID - 1] + ')');

  if (!lines.length) { props.setProperty('plan_' + today, '1'); return; }

  const msg = '<b>План на сегодня, ' + Utilities.formatDate(new Date(), TZ, 'dd.MM') + '</b>\n\n' +
              lines.join('\n') + '\n\nТекст пришлю за ' + PRE_MINUTES + ' минут до слота.';
  if (tg_(msg)) props.setProperty('plan_' + today, '1');
}

/** Сообщение с постом. */
function postMessage_(row, when, nowStr) {
  const text = String(row[C_TEXT - 1]);
  const late = when < nowStr;
  const head = (late ? '🔴 Пора публиковать' : '🟢 Через ' + PRE_MINUTES + ' минут') +
               ' — пост #' + row[C_ID - 1];
  return '<b>' + head + '</b>\n' +
         when.substring(8, 10) + '.' + when.substring(5, 7) + ', ' + when.substring(11) +
         ' · ' + esc_(String(row[C_RUBRIC - 1])) + '\n\n' +
         '<pre>' + esc_(text) + '</pre>\n' +
         text.length + ' символов' + (text.length > 500 ? ' ⚠️ лимит Threads 500' : '');
}

// ==================== ТРИГГЕРЫ ====================

/**
 * Сносит ВСЕ триггеры проекта и ставит ровно два.
 * Накопившиеся дубликаты триггеров — самая частая причина повторных уведомлений.
 */
function resetTriggers() {
  const old = ScriptApp.getProjectTriggers();
  old.forEach(t => ScriptApp.deleteTrigger(t));

  ScriptApp.newTrigger('watch').timeBased().everyMinutes(5).create();
  ScriptApp.newTrigger('morningPlan').timeBased().atHour(MORNING_H).nearMinute(0).everyDays(1).create();

  SpreadsheetApp.getActive().setSpreadsheetTimeZone(TZ);
  toast_('Удалено старых триггеров: ' + old.length + '. Установлено новых: 2');
}

function diagnose() {
  const trg = ScriptApp.getProjectTriggers()
    .map(t => '• ' + t.getHandlerFunction() + ' (' + t.getEventType() + ')').join('\n') || '• нет';
  const sh = sheet_();
  const last = Math.max(0, sh.getLastRow() - 1);
  const msg =
    'Лист: ' + sh.getName() + ', строк с постами: ' + last + '\n' +
    'Таймзона таблицы: ' + SpreadsheetApp.getActive().getSpreadsheetTimeZone() + '\n' +
    'Сейчас: ' + fmt_(new Date()) + ' (' + TZ + ')\n\n' +
    'Триггеры проекта:\n' + trg + '\n\n' +
    'Если триггеров больше двух — жмите «Переустановить триггеры».\n' +
    'Если дубли остались, значит второй скрипт живёт в отдельном проекте: ' +
    'script.google.com → Мои проекты.';
  SpreadsheetApp.getUi().alert('Диагностика', msg, SpreadsheetApp.getUi().ButtonSet.OK);
}

function sendTest() {
  const ok = tg_('<b>Проверка связи</b>\nБот контент-плана на месте. ' + fmt_(new Date()));
  toast_(ok ? 'Отправлено' : 'Не отправилось — проверьте TG_TOKEN и TG_CHAT_ID');
}

/** Прислать ближайший неотправленный пост прямо сейчас (не трогая расписание). */
function sendNextNow() {
  const sh = sheet_();
  const last = sh.getLastRow();
  const data = sh.getRange(FIRST_ROW, 1, last - FIRST_ROW + 1, HEADERS.length).getValues();
  for (let i = 0; i < data.length; i++) {
    const row = data[i];
    if (!row[C_ID - 1] || String(row[C_SENT - 1]).trim() !== '') continue;
    if (String(row[C_STATUS - 1]).trim() !== 'согласовано') continue;
    const when = dstr_(row[C_DATE - 1]) + ' ' + tstr_(row[C_TIME - 1]);
    if (tg_(postMessage_(row, when, fmt_(new Date())))) mark_(sh, i, new Date(), row[C_ID - 1]);
    toast_('Отправлен пост #' + row[C_ID - 1]);
    return;
  }
  toast_('Неотправленных постов нет');
}

function resetSentMarks() {
  const sh = sheet_();
  const last = sh.getLastRow();
  if (last >= FIRST_ROW) sh.getRange(FIRST_ROW, C_SENT, last - FIRST_ROW + 1, 1).clearContent();
  clearSentProps_();
  toast_('Отметки отправки сняты');
}

// ==================== СЛУЖЕБНОЕ ====================

function sheet_() {
  const ss = SpreadsheetApp.getActive();
  const byHeader = ss.getSheets().filter(s => String(s.getRange(1, 1).getValue()).trim() === 'ID');
  return byHeader.length ? byHeader[0] : ss.getSheets()[0];
}

function mark_(sh, idx, value, id) {
  sh.getRange(FIRST_ROW + idx, C_SENT).setValue(
    value instanceof Date ? Utilities.formatDate(value, TZ, 'yyyy-MM-dd HH:mm') : value);
  PropertiesService.getScriptProperties().setProperty('sent_' + id, '1');
  SpreadsheetApp.flush();
}

function clearSentProps_() {
  const props = PropertiesService.getScriptProperties();
  Object.keys(props.getProperties())
    .filter(k => k.indexOf('sent_') === 0 || k.indexOf('plan_') === 0)
    .forEach(k => props.deleteProperty(k));
}

function tg_(html) {
  if (TG_TOKEN.indexOf('ВСТАВЬТЕ') === 0) throw new Error('Не заполнены TG_TOKEN / TG_CHAT_ID в начале файла');
  const res = UrlFetchApp.fetch('https://api.telegram.org/bot' + TG_TOKEN + '/sendMessage', {
    method: 'post',
    contentType: 'application/json',
    muteHttpExceptions: true,
    payload: JSON.stringify({
      chat_id: TG_CHAT_ID,
      text: html,
      parse_mode: 'HTML',
      disable_web_page_preview: true
    })
  });
  const ok = res.getResponseCode() === 200 && JSON.parse(res.getContentText()).ok === true;
  if (!ok) console.error('Telegram: ' + res.getContentText());
  return ok;
}

function fmt_(d) { return Utilities.formatDate(d, TZ, 'yyyy-MM-dd HH:mm'); }

function dstr_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, TZ, 'yyyy-MM-dd');
  return String(v).trim();
}

function tstr_(v) {
  if (v instanceof Date) return Utilities.formatDate(v, TZ, 'HH:mm');
  const m = String(v).trim().match(/^(\d{1,2})[:.](\d{2})/);
  return m ? ('0' + m[1]).slice(-2) + ':' + m[2] : String(v).trim();
}

function esc_(s) { return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;'); }

function toast_(msg) {
  try { SpreadsheetApp.getActive().toast(msg, 'Threads-бот', 8); } catch (e) { console.log(msg); }
}
