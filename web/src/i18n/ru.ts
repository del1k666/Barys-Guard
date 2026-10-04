/**
 * Все строки интерфейса.
 *
 * Компоненты текста не содержат: добавить язык — значит добавить
 * соседний файл с тем же устройством, а не переписывать экраны.
 */
export const ru = {
  app: { name: "BarysGuard DLP" },

  nav: {
    main: "Основная навигация",
    overview: "Обзор",
    agents: "Агенты",
    menu: "Меню",
    logout: "Выйти",
    roles: { admin: "Администратор", operator: "Оператор" } as Record<string, string>,
  },

  common: {
    loading: "Загрузка",
    retry: "Повторить",
    cancel: "Отмена",
    none: "—",
  },

  errors: {
    forbidden: "Недостаточно прав для этого раздела.",
    notFound: "Запрошенный объект не найден или недоступен.",
    network: "Нет связи с сервером. Проверьте подключение.",
    unknown: "Что-то пошло не так.",
  },

  login: {
    title: "Вход в консоль",
    checking: "Проверяем сессию",
    username: "Логин",
    password: "Пароль",
    submit: "Войти",
    invalid: "Неверный логин или пароль",
  },

  password: {
    title: "Смена пароля",
    forced: "Вам выдан временный пароль. Задайте собственный, чтобы продолжить работу.",
    current: "Текущий пароль",
    next: "Новый пароль",
    repeat: "Повторите новый пароль",
    hint: "Не короче 12 символов.",
    submit: "Сменить пароль",
    mismatch: "Пароли не совпадают",
    wrongCurrent: "Текущий пароль неверен",
    done: "Пароль изменён",
  },

  status: {
    agent: {
      pending: "Ожидает",
      active: "Активен",
      offline: "Не в сети",
      quarantined: "Карантин",
      revoked: "Отозван",
    } as Record<string, string>,
    command: {
      queued: "В очереди",
      sent: "Отправлена",
      running: "Выполняется",
      done: "Выполнена",
      failed: "Ошибка",
      expired: "Просрочена",
    } as Record<string, string>,
  },

  commandType: {
    ping: "Проверка связи",
    refresh_config: "Обновить конфигурацию",
    collect_diagnostics: "Собрать диагностику",
  } as Record<string, string>,

  pagination: {
    label: "Постраничная навигация",
    prev: "Назад",
    next: "Вперёд",
    range: (from: string, to: string, total: string) => `${from}–${to} из ${total}`,
  },

  overview: {
    title: "Обзор",
    agents: "Агенты",
    total: "Всего",
    certificates: "Сертификаты, скоро истекающие",
    tokens: "Активные токены регистрации",
    commands: "Команды",
    queued: "В очереди",
    failed24h: "Неудачные за 24 часа",
    versions: "Версии агента",
    systems: "Операционные системы",
    noData: "Данных пока нет",
  },

  agents: {
    title: "Агенты",
    caption: "Список агентов",
    search: "Поиск по имени хоста",
    searchSubmit: "Найти",
    status: "Статус",
    group: "Группа",
    anyStatus: "Любой статус",
    anyGroup: "Любая группа",
    noGroup: "Без группы",
    reset: "Сбросить фильтры",
    columns: {
      host: "Хост",
      status: "Статус",
      os: "Система",
      group: "Группа",
      heartbeat: "Последняя связь",
      version: "Версия агента",
    },
    emptyFleet: "Агентов пока нет",
    emptyFleetHint: "Выпустите токен регистрации и установите агент на хост.",
    emptyFilter: "Ничего не найдено",
    emptyFilterHint: "Измените запрос или сбросьте фильтры.",
    emptyPage: "На этой странице агентов нет",
    emptyPageHint: "Список стал короче, чем номер страницы.",
    firstPage: "На первую страницу",
  },

  agent: {
    back: "← К списку агентов",
    facts: "Сведения",
    hostname: "Имя хоста",
    os: "Система",
    arch: "Архитектура",
    version: "Версия агента",
    group: "Группа",
    ip: "Последний IP",
    skew: "Расхождение часов",
    configVersion: "Версия конфигурации",
    enrolledAt: "Зарегистрирован",
    machineId: "Идентификатор машины",
    tags: "Теги",
    certificate: "Сертификат",
    noCertificate: "Сертификат не выдан",
    serial: "Серийный номер",
    fingerprint: "Отпечаток SHA-256",
    validFrom: "Действует с",
    validTo: "Действует до",
    revokedAt: "Отозван",
    history: "История команд",
    noCommands: "Команд ещё не было",
    historyColumns: {
      type: "Команда",
      status: "Статус",
      created: "Создана",
      completed: "Завершена",
      result: "Результат",
    },
    actions: "Действия",
    sendCommand: "Отправить команду",
    commandTitle: "Отправить команду",
    commandType: "Тип команды",
    commandSend: "Отправить",
    commandSent: "Команда поставлена в очередь",
    revoke: "Отозвать агента",
    revokeTitle: "Отозвать агента?",
    revokeWarning: (host: string) =>
      `Агент ${host} будет выведен из-под наблюдения: все его сертификаты отзываются. Отменить это нельзя.`,
    revokeReason: "Причина отзыва",
    revokeConfirm: "Отозвать",
    revoked: "Агент отозван",
  },
};
