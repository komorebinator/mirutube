// translations: the page in the household's language (behaviour#languages).
// The page's words are written in English in its code and looked up here;
// what the server says is English too, and translated by the same dictionary
// (decisions#page-translates). A `{}` in an entry stands for a value put in —
// a name, a number, what the server or a tool said — kept as it came.
//
// Imported first of all by app.js and importing nothing, so it reads the
// words of index.html before any other part of the page writes over them.

// ----------------------------------------------------------- the dictionary

// Russian for each English text. An entry that depends on a number gives the
// forms Russian needs, for one, a few and many; its key is the English form
// for many, as `tn()` is called with it.
const RU = {
  // top_bar
  "Connect a device": "Подключить устройство",
  "Scan to open MiruTube on a phone": "Отсканируйте, чтобы открыть MiruTube на телефоне",
  "QR code of the address": "QR-код адреса",
  "Open this address on another device": "Откройте этот адрес на другом устройстве",
  "Search": "Поиск",
  "Search by title": "Поиск по названию",
  "Upload": "Загрузить",
  "Upload · {} coming in": "Загрузить · загружается: {}",
  "Install MiruTube as an app": "Установить MiruTube как приложение",
  "Rescan library": "Пересканировать библиотеку",
  "Settings": "Настройки",
  "Close": "Закрыть",
  "Series": "Сериал",
  "Playlist": "Плейлист",
  "Video": "Видео",
  "Nothing found": "Ничего не найдено",

  // install_card and the settings section
  "Install MiruTube": "Установка MiruTube",
  "As an app MiruTube opens in a window of its own, with its icon, and without the browser around it.":
    "Как приложение MiruTube открывается в своём окне, со своим значком и без интерфейса браузера вокруг.",
  "Use HTTPS on this device": "Включите HTTPS на этом устройстве",
  "A browser installs only a secure site. MiruTube signs its own certificate: install it on this device once, and MiruTube opens here over HTTPS from then on.":
    "Браузер устанавливает только защищённые сайты. MiruTube подписывает свой сертификат сам: установите его на этом устройстве один раз, и дальше MiruTube будет открываться здесь по HTTPS.",
  "Download certificate": "Скачать сертификат",
  "Other devices": "Другие устройства",
  "Open over HTTPS": "Открыть по HTTPS",
  "Install": "Установка",
  "How to install": "Как установить",
  "Once MiruTube is open over HTTPS: ": "Когда MiruTube откроется по HTTPS: ",
  "MiruTube runs here as an installed app.": "Здесь MiruTube работает как установленное приложение.",
  "This device opens MiruTube over HTTPS, so it can be installed here as an app.":
    "Это устройство открывает MiruTube по HTTPS, поэтому его можно установить здесь как приложение.",
  "This is the computer MiruTube runs on: it can be installed here as an app without a certificate.":
    "Это компьютер, на котором работает MiruTube: здесь его можно установить как приложение без сертификата.",
  "This device opens MiruTube over plain HTTP. To install it as an app, install MiruTube’s certificate first.":
    "Это устройство открывает MiruTube по обычному HTTP. Чтобы установить его как приложение, сначала установите сертификат MiruTube.",
  "iPhone and iPad": "iPhone и iPad",
  "Windows — Chrome and Edge": "Windows — Chrome и Edge",
  "macOS — Safari and Chrome": "macOS — Safari и Chrome",
  "Linux — Chrome and Chromium": "Linux — Chrome и Chromium",
  "Press <b>Download certificate</b> above.": "Нажмите <b>Скачать сертификат</b> выше.",
  "Open <b>Settings → Security and privacy → More security settings → Encryption and credentials → Install a certificate → CA certificate</b>, press <b>Install anyway</b> and choose <code>MiruTube.crt</code>.":
    "Откройте <b>Настройки → Безопасность и конфиденциальность → Другие настройки безопасности → Шифрование и учётные данные → Установка сертификата → Сертификат ЦС</b>, нажмите <b>Всё равно установить</b> и выберите <code>MiruTube.crt</code>.",
  "Come back and press <b>Open over HTTPS</b>.": "Вернитесь сюда и нажмите <b>Открыть по HTTPS</b>.",
  "Press <b>Download certificate</b> above in Safari and allow the profile to download.":
    "Нажмите <b>Скачать сертификат</b> выше в Safari и разрешите загрузку профиля.",
  "Open <b>Settings → Profile Downloaded → Install</b>.":
    "Откройте <b>Настройки → Профиль загружен → Установить</b>.",
  "Open <b>Settings → General → About → Certificate Trust Settings</b> and turn on <b>MiruTube on …</b>.":
    "Откройте <b>Настройки → Основные → Об этом устройстве → Доверие сертификатам</b> и включите <b>MiruTube on …</b>.",
  "Press <b>Download certificate</b> above and open the file.": "Нажмите <b>Скачать сертификат</b> выше и откройте файл.",
  "Press <b>Install Certificate</b>, choose <b>Current User</b>, then <b>Place all certificates in the following store → Browse → Trusted Root Certification Authorities</b>, and finish; answer <b>Yes</b> to the warning.":
    "Нажмите <b>Установить сертификат</b>, выберите <b>Текущий пользователь</b>, затем <b>Поместить все сертификаты в следующее хранилище → Обзор → Доверенные корневые центры сертификации</b> и завершите; на предупреждение ответьте <b>Да</b>.",
  "Restart the browser and press <b>Open over HTTPS</b>.": "Перезапустите браузер и нажмите <b>Открыть по HTTPS</b>.",
  "Press <b>Download certificate</b> above and open the file: Keychain Access adds it.":
    "Нажмите <b>Скачать сертификат</b> выше и откройте файл: его добавит «Связка ключей».",
  "In Keychain Access double-click <b>MiruTube on …</b>, open <b>Trust</b> and set <b>When using this certificate</b> to <b>Always Trust</b>.":
    "В «Связке ключей» дважды щёлкните <b>MiruTube on …</b>, раскройте <b>Доверие</b> и для <b>При использовании этого сертификата</b> выберите <b>Всегда доверять</b>.",
  "Press <b>Open over HTTPS</b>.": "Нажмите <b>Открыть по HTTPS</b>.",
  "Open <code>chrome://certificate-manager</code>, go to <b>Local certificates → Installed by you</b>, and import <code>MiruTube.crt</code> under <b>Trusted certificates</b>.":
    "Откройте <code>chrome://certificate-manager</code>, перейдите в <b>Локальные сертификаты → Установленные вами</b> и импортируйте <code>MiruTube.crt</code> в разделе <b>Доверенные сертификаты</b>.",
  "Open <b>Settings → Privacy &amp; Security → Certificates → View Certificates → Authorities → Import</b>, choose <code>MiruTube.crt</code> and tick <b>Trust this CA to identify websites</b>.":
    "Откройте <b>Настройки → Приватность и защита → Сертификаты → Просмотр сертификатов → Центры сертификации → Импортировать</b>, выберите <code>MiruTube.crt</code> и отметьте <b>Доверять при идентификации веб-сайтов</b>.",
  "Open the browser menu <b>⋮ → Add to Home screen → Install</b>.":
    "Откройте меню браузера <b>⋮ → Добавить на главный экран → Установить</b>.",
  "Open the browser menu <b>⋮ → Add app to Home screen</b>.":
    "Откройте меню браузера <b>⋮ → Добавить приложение на главный экран</b>.",
  "In Safari press <b>Share → Add to Home Screen</b>.": "В Safari нажмите <b>Поделиться → На экран «Домой»</b>.",
  "In Safari choose <b>File → Add to Dock</b>.": "В Safari выберите <b>Файл → Добавить в Dock</b>.",
  "Firefox does not install sites as apps on a computer. Open this address in Chrome, Edge or another Chromium-based browser and install it from there.":
    "Firefox на компьютере не устанавливает сайты как приложения. Откройте этот адрес в Chrome, Edge или другом браузере на Chromium и установите оттуда.",
  "Press the install icon at the right end of the address bar, or open the menu <b>… → Apps → Install this site as an app</b>. If MiruTube is installed already, Edge offers to open it in the app instead.":
    "Нажмите значок установки в правом конце адресной строки или откройте меню <b>… → Приложения → Установить этот сайт как приложение</b>. Если MiruTube уже установлен, Edge предложит вместо этого открыть его в приложении.",
  "Press the install icon at the right end of the address bar, or open the menu <b>⋮ → Cast, save and share → Install page</b>. If MiruTube is installed already, the browser offers to open it in the app instead.":
    "Нажмите значок установки в правом конце адресной строки или откройте меню <b>⋮ → Трансляция, сохранение и отправка → Установить страницу</b>. Если MiruTube уже установлен, браузер предложит вместо этого открыть его в приложении.",

  // uploads_card and drop_zone
  "Videos come into the library from this device or from a link, and play as soon as each has arrived. Every device sees what is coming.":
    "Видео попадают в библиотеку с этого устройства или по ссылке и воспроизводятся, как только каждое загрузится. Все устройства видят, что загружается.",
  "From this PC": "С этого устройства",
  "Files…": "Файлы…",
  "Folder…": "Папка…",
  "Videos, subtitles, sound tracks or a whole folder — or drop them anywhere on MiruTube.":
    "Видео, субтитры, звуковые дорожки или целая папка — или перетащите их в любое место MiruTube.",
  "Link": "Ссылка",
  "A link to a video or a playlist": "Ссылка на видео или плейлист",
  "Add": "Добавить",
  "Save to": "Сохранить в",
  "Coming in": "Загружается",
  "Nothing is coming in.": "Ничего не загружается.",
  "Drop to upload": "Отпустите, чтобы загрузить",
  "Waiting": "В очереди",
  "Downloading": "Загружается",
  "Interrupted": "Прервано",
  "Seeding": "Раздаётся",
  "Done": "Готово",
  "Failed": "Ошибка",
  "Cancelled": "Отменено",
  "less than a minute left": "осталось меньше минуты",
  "{} min left": "осталось {} мин",
  "{} h left": "осталось {} ч",
  "{} of {}": "{} из {}",
  "{}/s": "{}/с",
  "{} peers": { one: "{} пир", few: "{} пира", many: "{} пиров", other: "{} пира" },
  "{} seeded": "роздано {}",
  "from {}": "с устройства {}",
  "Stop seeding": "Остановить раздачу",
  "Remove from list": "Убрать из списка",
  "Cancel this download?": "Отменить загрузку?",
  "Everything “{}” brought is deleted, what is already in the library included. Files that were there before stay.":
    "Всё, что принесла загрузка «{}», будет удалено, включая то, что уже в библиотеке. Файлы, которые были там раньше, останутся.",
  "Cancel download": "Отменить загрузку",
  "It could not be done": "Не удалось",
  "There is no library folder yet: add one in Settings, on the computer MiruTube runs on.":
    "Папки библиотеки пока нет: добавьте её в настройках на компьютере, где работает MiruTube.",

  // home
  "Continue watching": "Продолжить просмотр",
  "Scroll left": "Прокрутить влево",
  "Scroll right": "Прокрутить вправо",
  "Library": "Библиотека",
  "Can't play in the browser": "Не воспроизводится в браузере",
  "Series · {} episodes": { one: "Сериал · {} серия", few: "Сериал · {} серии", many: "Сериал · {} серий", other: "Сериал · {} серии" },
  "Playlist · {} videos": { one: "Плейлист · {} видео", few: "Плейлист · {} видео", many: "Плейлист · {} видео", other: "Плейлист · {} видео" },
  "Remove from library": "Убрать из библиотеки",
  "Remove from Continue watching": "Убрать из «Продолжить просмотр»",
  "Remove from Continue watching?": "Убрать из «Продолжить просмотр»?",
  "“{}” leaves the row. Its progress is forgotten, and its videos count as not started.":
    "«{}» уйдёт из ряда. Прогресс будет забыт, а его видео будут считаться непросмотренными.",
  "“{}” leaves the row. Its progress is forgotten, and it counts as not started.":
    "«{}» уйдёт из ряда. Прогресс будет забыт, а видео будет считаться непросмотренным.",
  "Forget": "Забыть",
  "It could not be removed": "Не удалось убрать",
  "Remove from the library?": "Убрать из библиотеки?",
  "“{}” leaves the library. Nothing is deleted from the disk: the folder can be brought back in Settings.":
    "«{}» уйдёт из библиотеки. С диска ничего не удаляется: папку можно вернуть в настройках.",
  "“{}” leaves the library. Nothing is deleted from the disk: the video can be brought back in Settings.":
    "«{}» уйдёт из библиотеки. С диска ничего не удаляется: видео можно вернуть в настройках.",
  "Remove": "Убрать",
  "This playlist is no longer in the library": "Этого плейлиста больше нет в библиотеке",

  // watch
  "Cancel": "Отмена",
  "Next: {} in {}": "Далее: {} через {}",
  "Playing on {}": "Играет на устройстве {}",
  "Watched": "Просмотрено",
  "This video is no longer in the library.": "Этого видео больше нет в библиотеке.",
  "This video can't be played in the browser: {}": "Это видео не воспроизводится в браузере: {}",
  "the sound track {} could not be loaded ({})": "не удалось загрузить звуковую дорожку {} ({})",
  "its conversion could not be started ({})": "не удалось запустить его преобразование ({})",
  "this browser cannot play it converted ({})": "этот браузер не может воспроизвести его преобразованным ({})",
  "Sound": "Звук",
  "File": "Файл",
  "Plays": "Воспроизведение",
  "{} fps": "{} кадр/с",
  "{}-bit": "{} бит",
  "mono": "моно",
  "stereo": "стерео",
  "{} channels": { one: "{} канал", few: "{} канала", many: "{} каналов", other: "{} канала" },
  "played as it is": "воспроизводится как есть",
  "sent in pieces — the video copied, the sound converted": "передаётся частями — видео копируется, звук преобразуется",
  "converted as it plays": "преобразуется во время воспроизведения",
  "converted as it plays — {}": "преобразуется во время воспроизведения — {}",
  "can't be played": "не воспроизводится",
  "can't be played: {}": "не воспроизводится: {}",
  "bytes": "Б",
  "KB": "КБ",
  "MB": "МБ",
  "GB": "ГБ",
  "TB": "ТБ",

  // settings
  "Library folders": "Папки библиотеки",
  "Full path of a folder": "Полный путь к папке",
  "The library folders are changed on the computer MiruTube runs on.":
    "Папки библиотеки меняются на компьютере, где работает MiruTube.",
  "Playback": "Воспроизведение",
  "Subtitle size": "Размер субтитров",
  "In proportion to the picture, on every device.": "Относительно картинки, на всех устройствах.",
  "Downloads": "Загрузки",
  "Seed torrents after downloading": "Раздавать торренты после загрузки",
  "Share what was downloaded with others, until Stop seeding. For every device.":
    "Делиться загруженным с другими, пока раздачу не остановят. Для всех устройств.",
  "Language": "Язык",
  "The language of MiruTube on every device.": "Язык MiruTube на всех устройствах.",
  "Browser's language": "Язык браузера",
  "Removed from the library": "Убрано из библиотеки",
  "Nothing was removed. A video or a folder removed from the library shows here, to bring back.":
    "Ничего не убрано. Видео или папка, убранные из библиотеки, появятся здесь, чтобы их можно было вернуть.",
  "Folder": "Папка",
  "No longer on the disk": "Больше нет на диске",
  "Shown as {}": "Показывается как {}",
  "{} free": "свободно {}",
  "Not available": "Недоступно",
  "No library folder yet.": "Папок библиотеки пока нет.",
  "Bring back": "Вернуть",
  "It could not be changed": "Не удалось изменить",
  "It could not be added: {}": "Не удалось добавить: {}",
  "It could not be removed: {}": "Не удалось убрать: {}",
  "Remove this library folder?": "Убрать эту папку библиотеки?",
  "{} — its videos leave the library. Nothing is deleted from the disk.":
    "{} — её видео уйдут из библиотеки. С диска ничего не удаляется.",
  "Type the full path of a folder.": "Введите полный путь к папке.",
  "It could not be brought back": "Не удалось вернуть",

  // now_playing
  "Seek": "Перемотка",
  "Nothing playing": "Ничего не играет",
  "on this device": "на этом устройстве",
  "on {}": "на устройстве {}",
  "Paused": "На паузе",
  "Last watched": "Последнее просмотренное",
  "Previous video": "Предыдущее видео",
  "Play": "Воспроизвести",
  "Pause": "Пауза",
  "Next video": "Следующее видео",
  "Subtitles": "Субтитры",
  "Audio": "Звук",
  "Mute": "Выключить звук",
  "Unmute": "Включить звук",
  "Volume": "Громкость",
  "Speed": "Скорость",
  "Devices": "Устройства",
  "Picture in picture": "Картинка в картинке",
  "Full screen": "Во весь экран",
  "Exit full screen": "Выйти из полноэкранного режима",
  "Off": "Выкл.",
  "Play on {}": "Воспроизвести на устройстве {}",
  "This device": "Это устройство",
  "Rename": "Переименовать",
  "Name this device": "Название устройства",
  "Other devices show it under this name.": "Другие устройства видят его под этим названием.",
  "Connecting…": "Подключение…",

  // confirm_dialog, and the device's name
  "OK": "ОК",
  "{} on {}": "{} на {}",
  "Browser": "Браузер",
};

// What the server says (decisions#page-translates), kept apart: only these
// are matched against a message with what was put in it.
const RU_SERVER = {
  "the server could not be reached": "сервер недоступен",
  "outside the library": "вне библиотеки",
  "not in the catalogue": "нет в каталоге",
  "no such device": "нет такого устройства",
  "no such video": "нет такого видео",
  "no such playlist": "нет такого плейлиста",
  "no such download": "нет такой загрузки",
  "no such folder": "нет такой папки",
  "not a library folder": "это не папка библиотеки",
  "the folder's full path is needed": "нужен полный путь к папке",
  "already a library folder": "это уже папка библиотеки",
  "inside or around the library folder {}": "внутри папки библиотеки {} или вокруг неё",
  "a library folder is removed in settings": "папка библиотеки убирается в настройках",
  "not removed": "не убрано",
  "the library folders are changed on the computer MiruTube runs on":
    "папки библиотеки меняются на компьютере, где работает MiruTube",
  "no such library folder: {}": "нет такой папки библиотеки: {}",
  "the library folder {} is no longer there": "папки библиотеки {} больше нет",
  "{} would lie outside the library": "{} оказался бы вне библиотеки",
  "{} is not supported: only videos, subtitles, sound tracks and folders are taken":
    "{} не поддерживается: принимаются только видео, субтитры, звуковые дорожки и папки",
  "no files": "нет файлов",
  "no link": "нет ссылки",
  "this upload is {}": "эта загрузка: {}",
  "more than the file's size": "больше размера файла",
  "torrents are not supported here: libtorrent is not installed":
    "торренты здесь не поддерживаются: libtorrent не установлен",
  "this is not a torrent file ({})": "это не торрент-файл ({})",
  "this magnet link cannot be read ({})": "эту magnet-ссылку не удаётся прочитать ({})",
  "the torrent could not be fetched: {}": "не удалось получить торрент: {}",
  "this is too large to be a torrent file": "это слишком большой файл для торрента",
  "this link is not supported: it is neither a web address nor a magnet link":
    "эта ссылка не поддерживается: это не веб-адрес и не magnet-ссылка",
  "links are not supported here: yt-dlp is not installed": "ссылки здесь не поддерживаются: yt-dlp не установлен",
  "this link is not supported: {}": "эта ссылка не поддерживается: {}",
  "it could not be resumed: {}": "не удалось продолжить: {}",
  "a file could not be moved into the library: {}": "не удалось переместить файл в библиотеку: {}",
  "it is not seeding": "раздача не идёт",
  "a finished download is not cancelled: its files stay": "завершённую загрузку нельзя отменить: её файлы остаются",
  "only a finished download leaves the list": "из списка убирается только завершённая загрузка",
  "yt-dlp gave no reason": "yt-dlp не назвал причину",
  "image subtitles ({}) cannot be shown as text": "субтитры-картинки ({}) нельзя показать как текст",
  "ffprobe failed": "ffprobe не смог прочитать файл",
  "no video stream or no duration": "нет видеопотока или длительности",
  "the video has no audio stream {}": "у видео нет звукового потока {}",
  "no sound can be read from {}": "из {} не удаётся прочитать звук",
  "ffmpeg took longer than {} seconds": "ffmpeg работал дольше {} секунд",
};

const DICTIONARIES = { ru: Object.assign({}, RU, RU_SERVER) };
const SERVER = { ru: RU_SERVER };
export const LANGUAGES = ["en", "ru"];

// ---------------------------------------------------------- the language

const SETTING_KEY = "mirutube.language";  // the shared setting last seen, so a page opens in it at once

function storedSetting() {
  try {
    return window.localStorage.getItem(SETTING_KEY) || "auto";
  } catch (e) {
    return "auto";
  }
}

// The language in use for a setting: `en` or `ru` as it says, and with `auto`
// the first of the browser's preferred languages that MiruTube has, English
// when none is.
function resolve(setting) {
  if (LANGUAGES.includes(setting)) return setting;
  const preferred = (navigator.languages && navigator.languages.length) ? navigator.languages : [navigator.language];
  for (const tag of preferred) {
    const base = String(tag || "").toLowerCase().split("-")[0];
    if (LANGUAGES.includes(base)) return base;
  }
  return "en";
}

let setting = storedSetting();
let lang = resolve(setting);
let plurals = new Intl.PluralRules(lang);

export const language = () => lang;

// --------------------------------------------------------------- lookups

function fill(text, values) {
  let i = 0;
  return text.replace(/\{\}/g, () => (i < values.length ? String(values[i++]) : "{}"));
}

// `text` in the language in use, each `{}` in it filled with `values` in order.
export function t(text, ...values) {
  const dict = DICTIONARIES[lang];
  let found = dict && Object.prototype.hasOwnProperty.call(dict, text) ? dict[text] : text;
  if (typeof found === "object") found = found.other || text;
  return fill(found, values);
}

// A text that depends on the number `n`: `one` and `many` its English forms,
// `{}` in them the number, written as the language writes numbers.
export function tn(n, one, many, ...values) {
  const dict = DICTIONARIES[lang];
  const found = dict && Object.prototype.hasOwnProperty.call(dict, many) ? dict[many] : null;
  const form = found === null ? (n === 1 ? one : many)
    : typeof found === "object" ? found[plurals.select(n)] || found.other || many : found;
  return fill(form, [formatNumber(n), ...values]);
}

// What the server said, in the language in use: an entry matching it whole,
// or one whose `{}` match what the server put in, which is kept as it came;
// a message with no entry as the server sent it.
let templates = null;      // [[regexp, translation]] of the entries with a `{}`, made when first needed

export function fromServer(message) {
  const dict = DICTIONARIES[lang];
  if (!dict || !message || typeof message !== "string") return message;
  if (typeof dict[message] === "string") return dict[message];
  if (!templates) {
    templates = Object.keys(SERVER.ru).filter((k) => k.includes("{}")).map((k) => [
      new RegExp("^" + k.split("{}").map((s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")).join("([\\s\\S]+?)") + "$"), k]);
  }
  for (const [re, key] of templates) {
    const m = message.match(re);
    // what was put in can be a message of the server's own, as a reason within a reason
    if (m) return fill(dict[key], m.slice(1).map((part) => fromServer(part)));
  }
  return message;
}

// ----------------------------------------------------------- formatting

export function formatNumber(n, digits = 0) {
  return new Intl.NumberFormat(lang, { minimumFractionDigits: digits, maximumFractionDigits: digits }).format(n);
}

// ------------------------------------------------------------ index.html

// The words of index.html, read once as written — in English — before any
// other part of the page changes them: each text and each attribute a person
// reads, with what was last put there, so a word the scripts have since
// written over is left to them.
const ATTRIBUTES = ["aria-label", "title", "placeholder", "alt"];
const words = [];

function collect() {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let node = walker.nextNode(); node; node = walker.nextNode()) {
    const english = node.nodeValue.replace(/\s+/g, " ").trim();
    if (!english || /^(script|style)$/i.test(node.parentNode.nodeName)) continue;
    const [, before, after] = node.nodeValue.match(/^(\s*)[\s\S]*?(\s*)$/);
    words.push({ node, english, wrap: (s) => before.replace(/\s+/g, " ") + s + after.replace(/\s+/g, " "), last: node.nodeValue });
  }
  for (const element of document.body.querySelectorAll(ATTRIBUTES.map((a) => "[" + a + "]").join(","))) {
    for (const attr of ATTRIBUTES) {
      const english = element.getAttribute(attr);
      if (english) words.push({ element, attr, english, last: english });
    }
  }
}

function translateDocument() {
  document.documentElement.lang = lang;
  for (const w of words) {
    if (w.node) {
      if (w.node.nodeValue !== w.last) continue;
      w.last = w.node.nodeValue = w.wrap(t(w.english));
    } else {
      if (w.element.getAttribute(w.attr) !== w.last) continue;
      w.last = t(w.english);
      w.element.setAttribute(w.attr, w.last);
    }
  }
}

// ----------------------------------------------------- a change of language

const changed = [];

// `fn` runs whenever the language in use changes, to draw again what it drew.
export function onLanguage(fn) {
  changed.push(fn);
}

// The household's `language` setting as it came (page#fields#shared_settings).
export function setLanguage(value) {
  setting = typeof value === "string" ? value : "auto";
  try {
    window.localStorage.setItem(SETTING_KEY, setting);
  } catch (e) { /* not kept: the page opens in the browser's language until it comes */ }
  const now = resolve(setting);
  if (now === lang) return;
  lang = now;
  plurals = new Intl.PluralRules(lang);
  translateDocument();
  for (const fn of changed) {
    try {
      fn();
    } catch (e) {
      console.error(e);
    }
  }
}

export const languageSetting = () => setting;

collect();
translateDocument();
