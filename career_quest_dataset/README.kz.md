# Career Quest — деректер жинағы

Синтетикалық деректер. Нақты адамдар немесе компаниялар жоқ.

**Деректердің кесінді күні:** `2026-10-01`. Осы күнді «бүгін» деп есептеңіз.
**Тарих кезеңі:** `2024-10-01` – `2026-09-30`.

## Файлдар

| Файл | Мазмұны | Көлемі |
|---|---|---|
| `skills.json` | Дағдылар каталогы, меңгеру деңгейлерінің шкаласы және грейдтер бойынша рөл талаптары | 60 дағды, 8 рөл × 4 грейд |
| `employees.json` | Қызметкерлердің профильдері | 200 |
| `events.json` | Даму іс-шараларының каталогы | 40 |
| `activity_history.csv` | Қатысу журналы | 2 743 жазба |

## Байланыстар

```text
employees.skills ─────────────┐
role_profiles.required_skills ├──> skills.skill_id
events.develops_skills ───────┤
events.prerequisites ─────────┘
employees.(role, grade) ──────> role_profiles.(role, grade)
employees.manager_id ─────────> employees.employee_id
activity_history.employee_id ─> employees.employee_id
activity_history.event_id ────> events.event_id
```

Барлық сілтеме жарамды. Идентификаторлар бірегей.

## skills.json

`proficiency_scale` — 0-ден 5-ке дейінгі меңгеру деңгейлерінің мағынасы. Деректер жинағындағы барлық дағды деңгейі осы шкаланы пайдаланады.

`skills[]`

| Өріс | Түрі | Ескертпелер |
|---|---|---|
| `skill_id` | string | Мысалы, `SK_SYSTEM_DESIGN` |
| `name` | string | |
| `type` | `hard` \| `soft` | |
| `category` | string | Есептерде топтастыруға арналған |
| `description` | string | |

`role_profiles[]` — рөл мен грейдтің әр тіркесіне бір жазбадан.

| Өріс | Түрі | Ескертпелер |
|---|---|---|
| `role` | string | 8 рөл |
| `grade` | `Junior` \| `Middle` \| `Senior` \| `Lead` | Осы ретпен |
| `required_skills` | object | Осы грейд үшін `skill_id → ең төменгі деңгей` |
| `critical_skills` | array | Осы грейдке сай болу үшін талапқа міндетті түрде сәйкес келуі керек дағдылар. Қызметтік өсу үшін негізгі көрсеткіш |

Бір грейдтен келесісіне өткен сайын талаптар төмендемейді.

## employees.json

| Өріс | Түрі | Ескертпелер |
|---|---|---|
| `employee_id` | string | `E0001` … `E0200` |
| `full_name` | string | Ойдан шығарылған есім |
| `department` | string | Әр рөлге бір бөлім |
| `role`, `grade` | string | `role_profiles` жазбасына сәйкес келеді |
| `manager_id` | string \| null | Сол бөлімдегі `Lead` грейдінің қызметкері. Бөлім басшылары үшін `null` |
| `hire_date` | date | |
| `tenure_months` | int | `hire_date` күнінен деректердің кесінді күніне дейінгі толық айлар саны |
| `work_format` | `office` \| `hybrid` \| `remote` | |
| `preferred_language` | `kk` \| `ru` \| `en` | Интерфейстің қалаулы тілі |
| `career_goal` | object \| null | `{target_role, target_grade}`. `null` — мақсат қойылмаған |
| `skills` | object | `skill_id → 0–5 деңгейі`. Көрсетілмеген дағдының деңгейі 0 деп есептеледі |
| `last_review_date` | date | Дағдыларды соңғы бағалау күні |

Дағды деңгейлері соңғы бағалау нәтижелерін көрсетеді. `last_review_date` күнінен кейін аяқталған іс-шаралар әлі есепке алынбаған.

## events.json

| Өріс | Түрі | Ескертпелер |
|---|---|---|
| `event_id` | string | `EV_001` … `EV_040` |
| `title`, `description` | string | |
| `type` | string | `compliance`, `onboarding`, `course`, `workshop`, `mentoring`, `certification`, `meetup` |
| `format` | `online` \| `offline` \| `self_paced` | |
| `duration_hours` | number | Жалпы еңбек шығыны |
| `mandatory` | bool | HR тағайындайды. Ұсыным нысаны емес |
| `target_roles`, `target_grades` | array | Іс-шара қандай рөлдер мен грейдтерге арналған |
| `develops_skills` | array | `{skill_id, gain, max_level}`: іс-шараны аяқтау дағды деңгейін `gain` мәніне арттырады, бірақ `max_level` деңгейінен асырмайды. Комплаенс-оқыту үшін массив бос болады |
| `prerequisites` | object | Қатысу үшін қажет `skill_id → ең төменгі деңгей` |
| `upcoming_sessions` | array of dates | Болашақ сессиялардың күндері. Кез келген уақытта қолжетімді `self_paced` форматы үшін массив бос болады |

## activity_history.csv

Бір жол — бір қызметкердің бір іс-шараға қатысуы.

| Баған | Түрі | Ескертпелер |
|---|---|---|
| `record_id` | string | `R000001` … |
| `employee_id`, `event_id` | string | |
| `date` | date | Сессия күні; `self_paced` форматы үшін — тіркелу немесе тағайындау күні |
| `due_date` | date \| empty | Тек міндетті іс-шаралар үшін |
| `status` | string | Төменде қараңыз |
| `completion_pct` | int 0–100 | |
| `score` | int 0–100 \| empty | Қорытынды баға. Тек курстар, сертификаттау және комплаенс-оқыту үшін |
| `feedback_rating` | int 1–5 \| empty | Қызметкердің іс-шараға берген бағасы. Міндетті емес |
| `assigned_by` | `self` \| `manager` \| `hr` | Қатысуды кім бастамашылық етті |

**Мәртебелер**

| Мәртебе | Мағынасы | `completion_pct` |
|---|---|---|
| `completed` | Аяқталды | 100 |
| `in_progress` | Басталды, бірақ әлі аяқталған жоқ | 0–95 |
| `dropped` | Басталды, кейін тоқтатылды | 5–95 |
| `no_show` | Қызметкер сессияға тіркелді, бірақ қатыспады. Тек кесте бойынша өтетін іс-шаралар үшін | 0 |
| `declined` | Қызметкер басшы немесе HR берген тапсырмадан бас тартты | 0 |
| `overdue` | Міндетті іс-шара `due_date` күніне дейін аяқталмады | 0–95 |

Жолдар `date`, `employee_id`, `event_id` бойынша сұрыпталған.

## Ережелер

- `completed` мәртебесінен кейін іс-шара қайталанбайды. Ерекшелік: `EV_036` — тұрақты түрде өтетін клуб.
- Тарихтағы ерікті іс-шаралар қызметкердің қазіргі немесе бұрынғы рөліне, грейдіне және қатысу талаптарына әрқашан сәйкес келеді.
- Жаңа қызметкерлер жұмысының алғашқы айында `EV_004` іс-шарасын аяқтайды.
- Бағалау үшін дәл осы форматтағы қосымша қызметкер профильдері мен тарих жазбалары пайдаланылады. Шешім оларды жүктей алуы керек.
