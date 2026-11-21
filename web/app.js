"use strict";

const STORAGE_KEY = "roco-calc-battle-v1";
const OLD_STORAGE_KEY = "roco-calc-team-v1";
const STAT_NAMES = {
  hp: "生命",
  atk: "物攻",
  def: "物防",
  spa: "魔攻",
  spd: "魔防",
  spe: "速度",
};
const STAT_ORDER = ["hp", "atk", "def", "spa", "spd", "spe"];
const BUFF_KEYS = ["atk", "def", "spa", "spd", "spe"];
const TALENT_VALUES = [0, 7, 8, 9, 10];
const BUFF_MIN = -6;
const BUFF_MAX = 6;
const KNOCKOUT_TEXT = {
  certain: "一定击倒",
  possible: "可能击倒",
  impossible: "打不倒",
};
const NUMBER_FORMAT = new Intl.NumberFormat("zh-CN");

let meta = null;
let skillByName = new Map();
let actionSkillByName = new Map();
let petOptions = [];
let state = null;
let calcTimer = null;
let requestId = 0;
let battleAnalysisId = 0;
let battleDraft = null;
let battlePreBase = null;
let battleDraftDamageEvents = [];
let battleDraftHpEvents = [];
let battleDraftReplacements = { our: null, opp: null };
let battleStatusText = {};
let battleDraftSig = "";
let latestTeamResult = null;
let ocrStatus = null;
let ocrResult = null;

function $(id) {
  return document.getElementById(id);
}

function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) {
    node.className = className;
  }
  if (text !== undefined) {
    node.textContent = text;
  }
  return node;
}

function thumb(src, name, className) {
  const box = element("span", `thumb ${className || ""}`.trim());
  const label = name || "?";
  box.title = label;
  if (src) {
    const image = document.createElement("img");
    image.src = src;
    image.alt = label;
    image.loading = "lazy";
    image.decoding = "async";
    image.addEventListener("error", () => {
      image.remove();
      box.classList.add("thumb-empty");
      box.textContent = label.slice(0, 1);
    });
    box.append(image);
  } else {
    box.classList.add("thumb-empty");
    box.textContent = label.slice(0, 1);
  }
  return box;
}

function freshBattle() {
  return { initial: null, turns: [], result: null, inference: null };
}

function battleStarted() {
  return Boolean(state && state.battle && state.battle.initial);
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}

function petLabel(pet) {
  return pet.form ? `${pet.name}（${pet.form}）` : pet.name;
}

function summaryLabel(data) {
  return data.form ? `${data.pet}（${data.form}）` : data.pet;
}

function numberText(value) {
  if (typeof value !== "number" || !Number.isFinite(value)) {
    return "—";
  }
  return Number.isInteger(value) ? NUMBER_FORMAT.format(value) : String(value);
}

function rangeText(value, suffix = "") {
  if (value === null || value === undefined) {
    return "—";
  }
  if (typeof value === "number") {
    return `${numberText(value)}${suffix}`;
  }
  if (typeof value !== "object") {
    return `${String(value)}${suffix}`;
  }
  const { min, max } = value;
  if (typeof min !== "number" || typeof max !== "number") {
    return "—";
  }
  if (min === max) {
    return `${numberText(min)}${suffix}`;
  }
  return `${numberText(min)}～${numberText(max)}${suffix}`;
}

function formatBuff(value) {
  if (value > 0) {
    return `+${value}`;
  }
  if (value < 0) {
    return `−${Math.abs(value)}`;
  }
  return "0";
}

function findPetIndex(name) {
  for (let index = 0; index < meta.pets.length; index += 1) {
    const pet = meta.pets[index];
    if (pet.name === name && !pet.form) {
      return index;
    }
  }
  for (let index = 0; index < meta.pets.length; index += 1) {
    if (meta.pets[index].name === name) {
      return index;
    }
  }
  return -1;
}

function learnableSkills(petIndex) {
  const pet = meta.pets[petIndex];
  return pet.action_skills || pet.skills || [];
}

function filterSkills(petIndex, names) {
  const learnable = new Set(learnableSkills(petIndex));
  return [...new Set(names)].filter((name) => learnable.has(name));
}

function zeroTalents() {
  const result = {};
  for (const key of STAT_ORDER) {
    result[key] = 0;
  }
  return result;
}

function zeroBuffs() {
  const result = {};
  for (const key of BUFF_KEYS) {
    result[key] = 0;
  }
  return result;
}

function countNonzeroTalents(slot) {
  return STAT_ORDER.filter((key) => slot.talents[key] > 0).length;
}

function safeSlotIndex(value) {
  const number = Number(value);
  return Number.isInteger(number) && number >= 0 && number < 6 ? number : 0;
}

function sanitizeBuffs(raw) {
  const result = zeroBuffs();
  if (!raw || typeof raw !== "object") {
    return result;
  }
  for (const key of BUFF_KEYS) {
    const value = Number(raw[key]);
    if (Number.isInteger(value) && value >= BUFF_MIN && value <= BUFF_MAX) {
      result[key] = value;
    }
  }
  return result;
}

function sanitizeTalents(raw) {
  const result = zeroTalents();
  if (raw && typeof raw === "object") {
    for (const key of STAT_ORDER) {
      const value = Number(raw[key]);
      if (TALENT_VALUES.includes(value)) {
        result[key] = value;
      }
    }
  }
  let nonzero = 0;
  for (const key of STAT_ORDER) {
    if (result[key] > 0) {
      nonzero += 1;
      if (nonzero > 3) {
        result[key] = 0;
      }
    }
  }
  return result;
}

function validSlotNumber(value) {
  return Number.isInteger(value) && value >= 0 && value < 6;
}

function sanitizeRecord(raw) {
  if (!raw || typeof raw !== "object") {
    return null;
  }

  if (raw.type === "damage") {
    const attackerSide =
      raw.attackerSide === "our"
        ? "our"
        : raw.attackerSide === "opp"
          ? "opp"
          : null;
    if (!attackerSide) {
      return null;
    }

    const attackerSlot = Number(raw.attackerSlot);
    const defenderSlot = Number(raw.defenderSlot);
    if (!validSlotNumber(attackerSlot) || !validSlotNumber(defenderSlot)) {
      return null;
    }

    const skill = typeof raw.skill === "string" ? raw.skill.trim() : "";
    if (!skill) {
      return null;
    }

    const damage = Number(raw.damage);
    if (!Number.isInteger(damage) || damage < 0) {
      return null;
    }

    let hpPercent = null;
    if (
      raw.hpPercent !== null &&
      raw.hpPercent !== undefined &&
      raw.hpPercent !== ""
    ) {
      const value = Number(raw.hpPercent);
      if (!Number.isInteger(value) || value < 0 || value > 100) {
        return null;
      }
      hpPercent = value;
    }

    let remainingHp = null;
    if (
      raw.remainingHp !== null &&
      raw.remainingHp !== undefined &&
      raw.remainingHp !== ""
    ) {
      const value = Number(raw.remainingHp);
      if (!Number.isInteger(value) || value < 0) {
        return null;
      }
      remainingHp = value;
    }

    if (attackerSide === "our") {
      remainingHp = null;
    } else {
      hpPercent = null;
    }

    let starfall = 0;
    if (raw.starfall !== null && raw.starfall !== undefined) {
      const value = Number(raw.starfall);
      if (Number.isInteger(value) && value >= 0 && value <= 99) {
        starfall = value;
      }
    }

    let hits = 1;
    if (raw.hits !== null && raw.hits !== undefined) {
      const value = Number(raw.hits);
      if (Number.isInteger(value) && value >= 1 && value <= 99) {
        hits = value;
      }
    }

    return {
      id: 0,
      type: "damage",
      attackerSide,
      attackerSlot,
      defenderSlot,
      skill,
      damage,
      hpPercent,
      remainingHp,
      weather: raw.weather === "雨天" ? "雨天" : "",
      attackerBuffs: sanitizeBuffs(raw.attackerBuffs),
      defenderBuffs: sanitizeBuffs(raw.defenderBuffs),
      starfall,
      hits,
      matched: null,
      kept: null,
      message: null,
    };
  }

  if (raw.type === "hp_change") {
    const side =
      raw.side === "our" ? "our" : raw.side === "opp" ? "opp" : null;
    if (!side) {
      return null;
    }
    const slot = Number(raw.slot);
    if (!validSlotNumber(slot)) {
      return null;
    }

    if (side === "opp") {
      const percent = Number(raw.hpPercent);
      if (!Number.isInteger(percent) || percent < 0 || percent > 100) {
        return null;
      }
      let delta = null;
      if (
        raw.hpDelta !== null &&
        raw.hpDelta !== undefined &&
        raw.hpDelta !== ""
      ) {
        const value = Number(raw.hpDelta);
        if (!Number.isInteger(value)) {
          return null;
        }
        delta = value;
      }
      return {
        id: 0,
        type: "hp_change",
        side,
        slot,
        hpPercent: percent,
        hpDelta: delta,
        matched: null,
        kept: null,
        message: null,
      };
    }

    const hp = Number(raw.hp);
    if (!Number.isInteger(hp) || hp < 0) {
      return null;
    }
    return {
      id: 0,
      type: "hp_change",
      side,
      slot,
      hp,
      matched: null,
      kept: null,
      message: null,
    };
  }

  return null;
}

function sanitizeRecords(rawRecords, rawNextId) {
  const records = [];
  const usedIds = new Set();
  let maxId = 0;

  if (Array.isArray(rawRecords)) {
    for (const raw of rawRecords) {
      const record = sanitizeRecord(raw);
      if (!record) {
        continue;
      }
      let id = Number(raw.id);
      if (!Number.isInteger(id) || id <= 0 || usedIds.has(id)) {
        id = maxId + 1;
        while (usedIds.has(id)) {
          id += 1;
        }
      }
      record.id = id;
      usedIds.add(id);
      maxId = Math.max(maxId, id);
      records.push(record);
    }
  }

  let nextId = Number(rawNextId);
  if (!Number.isInteger(nextId) || nextId <= maxId) {
    nextId = maxId + 1;
  }
  return { records, nextRecordId: nextId };
}

function freshOurSlot(petIndex) {
  return {
    petIndex,
    nature: null,
    talents: zeroTalents(),
    currentHp: null,
    buffs: zeroBuffs(),
    skills: [],
  };
}

function freshOppSlot(petIndex) {
  return {
    petIndex,
    buffs: zeroBuffs(),
    skills: [],
  };
}

function getSlot(side, index) {
  return side === "our" ? state.ourTeam[index] : state.oppTeam[index];
}

function setSlot(side, index, slot) {
  if (side === "our") {
    state.ourTeam[index] = slot;
  } else {
    state.oppTeam[index] = slot;
  }
}

function defaultState() {
  const result = {
    weather: "",
    activeOur: 0,
    activeOpp: 0,
    ourTeam: Array(6).fill(null),
    oppTeam: Array(6).fill(null),
    records: [],
    nextRecordId: 1,
    battle: freshBattle(),
  };

  const water = findPetIndex("水灵");
  if (water >= 0) {
    const slot = freshOurSlot(water);
    slot.nature = meta.natures.some((nature) => nature.name === "聪明")
      ? "聪明"
      : null;
    slot.talents.spa = 10;
    slot.skills = filterSkills(water, ["气泡", "水炮", "天洪", "水弹枪"]);
    result.ourTeam[0] = slot;
  }

  const pal = findPetIndex("龙息帕尔");
  if (pal >= 0) {
    const slot = freshOppSlot(pal);
    slot.skills = filterSkills(pal, ["龙之利爪", "升龙咆哮"]);
    result.oppTeam[0] = slot;
  }

  const qilui = findPetIndex("奇丽花");
  if (qilui >= 0) {
    result.oppTeam[1] = freshOppSlot(qilui);
  }

  return result;
}

function sanitizeTeam(rawTeam, ours) {
  const result = Array(6).fill(null);
  if (!Array.isArray(rawTeam)) {
    return result;
  }

  for (let index = 0; index < 6 && index < rawTeam.length; index += 1) {
    const raw = rawTeam[index];
    if (!raw || typeof raw !== "object") {
      continue;
    }
    const petIndex = Number(raw.petIndex);
    if (
      !Number.isInteger(petIndex) ||
      petIndex < 0 ||
      petIndex >= meta.pets.length
    ) {
      continue;
    }

    const slot = ours ? freshOurSlot(petIndex) : freshOppSlot(petIndex);
    slot.buffs = sanitizeBuffs(raw.buffs);

    let names = Array.isArray(raw.skills) ? raw.skills : [];
    names = names.filter((name) => typeof name === "string");
    slot.skills = filterSkills(petIndex, names);
    if (ours) {
      slot.skills = slot.skills.slice(0, 4);
    }

    if (ours) {
      if (
        typeof raw.nature === "string" &&
        meta.natures.some((nature) => nature.name === raw.nature)
      ) {
        slot.nature = raw.nature;
      }
      slot.talents = sanitizeTalents(raw.talents);

      if (
        raw.currentHp === null ||
        raw.currentHp === undefined ||
        raw.currentHp === ""
      ) {
        slot.currentHp = null;
      } else {
        const hp = Number(raw.currentHp);
        if (Number.isInteger(hp) && hp >= 0) {
          slot.currentHp = hp;
        }
      }
    }

    result[index] = slot;
  }

  return result;
}

function readStorage(key) {
  try {
    const text = localStorage.getItem(key);
    return text ? JSON.parse(text) : null;
  } catch (error) {
    return null;
  }
}

function sanitizeBattle(raw) {
  if (!raw || typeof raw !== "object") {
    return freshBattle();
  }
  return {
    initial:
      raw.initial && typeof raw.initial === "object" ? raw.initial : null,
    turns: Array.isArray(raw.turns) ? raw.turns : [],
    result:
      raw.result && typeof raw.result === "object" ? raw.result : null,
    inference: null,
  };
}

function findPetIndexByRef(name, form) {
  const wantedForm = form || "";
  for (let index = 0; index < meta.pets.length; index += 1) {
    const pet = meta.pets[index];
    if (pet.name === name && (pet.form || "") === wantedForm) {
      return index;
    }
  }
  return findPetIndex(name);
}

function teamFromBattleInitial(initial) {
  const ourTeam = Array(6).fill(null);
  const oppTeam = Array(6).fill(null);
  if (!initial || typeof initial !== "object") {
    return { ourTeam, oppTeam, activeOur: 0, activeOpp: 0 };
  }
  const ourRaw = Array.isArray(initial.our_team) ? initial.our_team : [];
  const oppRaw = Array.isArray(initial.opponent_team) ? initial.opponent_team : [];
  for (let index = 0; index < 6; index += 1) {
    const item = ourRaw[index];
    if (item && typeof item === "object" && item.pet) {
      const petIndex = findPetIndexByRef(item.pet, item.form);
      if (petIndex >= 0) {
        const slot = freshOurSlot(petIndex);
        if (meta.natures.some((nature) => nature.name === item.nature)) {
          slot.nature = item.nature;
        }
        slot.talents = sanitizeTalents(item.talents);
        slot.skills = filterSkills(
          petIndex,
          Array.isArray(item.skills) ? item.skills : []
        ).slice(0, 4);
        slot.currentHp = null;
        ourTeam[index] = slot;
      }
    }
    const oppItem = oppRaw[index];
    if (oppItem && typeof oppItem === "object" && oppItem.pet) {
      const petIndex = findPetIndexByRef(oppItem.pet, oppItem.form);
      if (petIndex >= 0) {
        const slot = freshOppSlot(petIndex);
        const skills = Array.isArray(oppItem.suspected_skills)
          ? oppItem.suspected_skills
          : oppItem.skills;
        slot.skills = filterSkills(
          petIndex,
          Array.isArray(skills) ? skills : []
        );
        oppTeam[index] = slot;
      }
    }
  }
  return {
    ourTeam,
    oppTeam,
    activeOur: safeSlotIndex(initial.active_our),
    activeOpp: safeSlotIndex(initial.active_opp),
  };
}

function initialFromTeamConfig() {
  const activeOur = safeSlotIndex(state.activeOur);
  const activeOpp = safeSlotIndex(state.activeOpp);
  if (!state.ourTeam[activeOur]) {
    throw new Error("请先在我方选择一个首发精灵。");
  }
  if (!state.oppTeam[activeOpp]) {
    throw new Error("请先在对方选择一个首发精灵。");
  }
  const ourTeam = state.ourTeam.map((slot) => {
    if (!slot) {
      return null;
    }
    const pet = meta.pets[slot.petIndex];
    return {
      pet: pet.name,
      form: pet.form || "",
      nature: slot.nature,
      talents: slot.talents,
      skills: slot.skills.map((name) => name),
      energy: 10,
      max_energy: 10,
    };
  });
  const opponentTeam = state.oppTeam.map((slot) => {
    if (!slot) {
      return null;
    }
    const pet = meta.pets[slot.petIndex];
    return {
      pet: pet.name,
      form: pet.form || "",
      suspected_skills: slot.skills.map((name) => name),
      energy: 10,
      max_energy: 10,
    };
  });
  return {
    our_team: ourTeam,
    opponent_team: opponentTeam,
    active_our: activeOur,
    active_opp: activeOpp,
    magic_points: { our: 4, opp: 4 },
  };
}

function zeroBuffSet() {
  return {
    temporary: zeroBuffs(),
    permanent: zeroBuffs(),
  };
}

function initialSnapshotFromInitial(initial) {
  const ourPets = Array(6).fill(null);
  const oppPets = Array(6).fill(null);
  const ourRaw = Array.isArray(initial.our_team) ? initial.our_team : [];
  const oppRaw = Array.isArray(initial.opponent_team) ? initial.opponent_team : [];
  for (let index = 0; index < 6; index += 1) {
    if (ourRaw[index]) {
      const maxHp =
        latestTeamResult &&
        latestTeamResult.our_team &&
        latestTeamResult.our_team[index] &&
        latestTeamResult.our_team[index].max_hp
          ? latestTeamResult.our_team[index].max_hp
          : 1;
      ourPets[index] = {
        hp: maxHp,
        max_hp: maxHp,
        energy: ourRaw[index].energy ?? 10,
        max_energy: ourRaw[index].max_energy ?? 10,
        buffs: zeroBuffSet(),
        statuses: [],
      };
    }
    if (oppRaw[index]) {
      oppPets[index] = {
        hp_percent: 100,
        energy: oppRaw[index].energy ?? 10,
        max_energy: oppRaw[index].max_energy ?? 10,
        buffs: zeroBuffSet(),
        statuses: [],
      };
    }
  }
  return {
    active_our: safeSlotIndex(initial.active_our),
    active_opp: safeSlotIndex(initial.active_opp),
    weather: null,
    our_marks: { positive: null, negative: null },
    opp_marks: { positive: null, negative: null },
    magic_points: { our: 4, opp: 4 },
    our_pets: ourPets,
    opp_pets: oppPets,
  };
}

function currentBattleSnapshot() {
  if (
    state.battle.inference &&
    state.battle.inference.snapshot
  ) {
    return state.battle.inference.snapshot;
  }
  if (
    state.battle.inference &&
    state.battle.inference.initial_snapshot &&
    state.battle.turns.length === 0
  ) {
    return state.battle.inference.initial_snapshot;
  }
  if (state.battle.initial) {
    return initialSnapshotFromInitial(state.battle.initial);
  }
  if (battleDraft) {
    return battleDraft;
  }
  return null;
}

function restoreState() {
  const raw = readStorage(STORAGE_KEY) || readStorage(OLD_STORAGE_KEY);
  if (!raw || typeof raw !== "object") {
    state = defaultState();
    return;
  }

  const battle = sanitizeBattle(raw.battle);
  const fromInitial = battle.initial
    ? teamFromBattleInitial(battle.initial)
    : null;
  state = {
    weather: raw.weather === "雨天" ? "雨天" : "",
    activeOur: fromInitial
      ? fromInitial.activeOur
      : safeSlotIndex(raw.activeOur),
    activeOpp: fromInitial
      ? fromInitial.activeOpp
      : safeSlotIndex(raw.activeOpp),
    ourTeam: fromInitial
      ? fromInitial.ourTeam
      : sanitizeTeam(raw.ourTeam, true),
    oppTeam: fromInitial
      ? fromInitial.oppTeam
      : sanitizeTeam(raw.oppTeam, false),
    records: [],
    nextRecordId: 1,
    battle,
  };
}

function saveState() {
  if (!state) {
    return;
  }
  try {
    const saved = {
      ...state,
      battle: {
        ...state.battle,
        inference: null,
      },
    };
    localStorage.setItem(STORAGE_KEY, JSON.stringify(saved));
  } catch (error) {
    // 浏览器存储过期或空间不足时，页面仍然可以正常计算。
  }
}

function setStatus(text, isError = false) {
  const node = $("status");
  node.textContent = text || "";
  node.classList.toggle("error", Boolean(isError));
  node.hidden = !text;
}

function slotElementId(side, index) {
  return `slot-${side}-${index}`;
}

function createPetSelect(side, index, slot) {
  const select = document.createElement("select");
  select.className = "pet-select";

  const empty = document.createElement("option");
  empty.value = "";
  empty.textContent = "— 选择精灵 —";
  select.append(empty);

  const options = document.createDocumentFragment();
  for (const item of petOptions) {
    const option = document.createElement("option");
    option.value = String(item.index);
    option.textContent = item.label;
    options.append(option);
  }
  select.append(options);
  select.value = slot ? String(slot.petIndex) : "";
  select.addEventListener("change", () => {
    changePet(side, index, select.value);
  });
  return select;
}

function changePet(side, index, value) {
  let next = null;
  if (value !== "") {
    const petIndex = Number(value);
    if (
      !Number.isInteger(petIndex) ||
      petIndex < 0 ||
      petIndex >= meta.pets.length
    ) {
      return;
    }
    next =
      side === "our" ? freshOurSlot(petIndex) : freshOppSlot(petIndex);
  }
  setSlot(side, index, next);
  rerenderSlot(side, index, true);
  scheduleCalculate();
}

function field(labelText, control) {
  const wrapper = element("label", "field");
  wrapper.append(element("span", "field-label", labelText), control);
  return wrapper;
}

function buildTalents(slot) {
  const wrapper = element("div", "talents");
  const head = element("div", "field-title");
  head.append(element("span", "", "天分"));
  const count = element("span", "hint", `已选 ${countNonzeroTalents(slot)} / 3`);
  head.append(count);
  wrapper.append(head);

  const grid = element("div", "talent-grid");
  for (const key of STAT_ORDER) {
    const cell = element("label", "talent");
    cell.append(element("span", "talent-name", STAT_NAMES[key]));

    const select = document.createElement("select");
    for (const value of TALENT_VALUES) {
      const option = document.createElement("option");
      option.value = String(value);
      option.textContent = String(value);
      select.append(option);
    }
    select.value = String(slot.talents[key]);
    select.addEventListener("change", () => {
      const value = Number(select.value);
      const oldValue = slot.talents[key];
      if (
        value > 0 &&
        oldValue === 0 &&
        countNonzeroTalents(slot) >= 3
      ) {
        select.value = String(oldValue);
        setStatus("最多只能有三项天分有加成。", true);
        return;
      }
      slot.talents[key] = value;
      count.textContent = `已选 ${countNonzeroTalents(slot)} / 3`;
      scheduleCalculate();
    });

    cell.append(select);
    grid.append(cell);
  }
  wrapper.append(grid);
  return wrapper;
}

function buildBuffs(values) {
  const wrapper = element("div", "buffs");
  const head = element("div", "field-title");
  head.append(element("span", "", "增减益"));
  head.append(element("span", "hint", "四维每层 10%，速度每层 10 点"));
  wrapper.append(head);

  const grid = element("div", "buff-grid");
  for (const key of BUFF_KEYS) {
    const cell = element("div", "buff-cell");
    cell.append(element("span", "buff-name", STAT_NAMES[key]));

    const controls = element("div", "buff-controls");
    const minus = element("button", "buff-btn", "−");
    minus.type = "button";
    minus.setAttribute("aria-label", `${STAT_NAMES[key]}减少一层`);

    const shown = element("span", "buff-value");
    const plus = element("button", "buff-btn", "＋");
    plus.type = "button";
    plus.setAttribute("aria-label", `${STAT_NAMES[key]}增加一层`);

    const refresh = () => {
      shown.textContent = formatBuff(values[key]);
      minus.disabled = values[key] <= BUFF_MIN;
      plus.disabled = values[key] >= BUFF_MAX;
    };

    minus.addEventListener("click", () => {
      if (values[key] <= BUFF_MIN) {
        return;
      }
      values[key] -= 1;
      refresh();
      scheduleCalculate();
    });
    plus.addEventListener("click", () => {
      if (values[key] >= BUFF_MAX) {
        return;
      }
      values[key] += 1;
      refresh();
      scheduleCalculate();
    });

    refresh();
    controls.append(minus, shown, plus);
    cell.append(controls);
    grid.append(cell);
  }
  wrapper.append(grid);
  return wrapper;
}

function skillLabel(name) {
  const info = skillByName.get(name);
  if (info) {
    const power = info.power === null || info.power === undefined
      ? "?"
      : info.power;
    return `${name}（${info.element} · ${info.damage_class} · 威力 ${power}）`;
  }
  const action = actionSkillByName.get(name);
  if (action) {
    return `${name}（${action.category} · ${action.element}）`;
  }
  return name;
}

function buildSkillsSection(side, slot, index) {
  const wrapper = element("div", "skills-block");
  const head = element("div", "field-title");
  head.append(element("span", "", "技能"));
  head.append(
    element(
      "span",
      "hint",
      side === "our" ? `${slot.skills.length} / 4` : `${slot.skills.length} 个`
    )
  );
  wrapper.append(head);

  const list = element("ul", "skill-list");
  if (!slot.skills.length) {
    list.append(element("li", "empty-note", "还没有选择技能。"));
  }
  slot.skills.forEach((name, position) => {
    const info = skillByName.get(name) || actionSkillByName.get(name);
    const item = element("li", "skill-chip");
    item.append(thumb(info ? info.icon : null, name, "skill-chip-icon"));
    item.append(element("span", "skill-chip-label", skillLabel(name)));

    const remove = element("button", "skill-remove", "×");
    remove.type = "button";
    remove.title = `移除 ${name}`;
    remove.setAttribute("aria-label", `移除技能 ${name}`);
    remove.addEventListener("click", () => {
      slot.skills.splice(position, 1);
      rerenderSlot(side, index, true);
      scheduleCalculate();
    });

    item.append(remove);
    list.append(item);
  });
  wrapper.append(list);

  if (side === "our" && slot.skills.length >= 4) {
    wrapper.append(
      element("p", "hint", "我方每只最多 4 个技能，先移除一个才能添加。")
    );
    return wrapper;
  }

  const learnable = learnableSkills(slot.petIndex);
  const remaining = learnable.filter((name) => !slot.skills.includes(name));
  if (!remaining.length) {
    wrapper.append(element("p", "hint", "没有其他可学的攻击技能了。"));
    return wrapper;
  }

  const addRow = element("div", "skill-add");
  const select = document.createElement("select");
  select.className = "skill-select";
  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "选择可学技能";
  select.append(placeholder);

  const options = document.createDocumentFragment();
  for (const name of remaining) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent = skillLabel(name);
    options.append(option);
  }
  select.append(options);

  const button = element("button", "btn btn-small", "添加");
  button.type = "button";
  button.addEventListener("click", () => {
    const name = select.value;
    if (!name || slot.skills.includes(name)) {
      return;
    }
    slot.skills.push(name);
    rerenderSlot(side, index, true);
    scheduleCalculate();
  });

  addRow.append(select, button);
  wrapper.append(addRow);
  return wrapper;
}

function buildOurFields(slot, index) {
  const fragment = document.createDocumentFragment();

  const row = element("div", "field-grid");
  const natureSelect = document.createElement("select");
  const noNature = document.createElement("option");
  noNature.value = "";
  noNature.textContent = "无性格";
  natureSelect.append(noNature);
  for (const nature of meta.natures) {
    const option = document.createElement("option");
    option.value = nature.name;
    option.textContent = `${nature.name}（${nature.up_name}↑ ${nature.down_name}↓）`;
    natureSelect.append(option);
  }
  natureSelect.value = slot.nature || "";
  natureSelect.addEventListener("change", () => {
    slot.nature = natureSelect.value || null;
    scheduleCalculate();
  });
  row.append(field("性格", natureSelect));
  fragment.append(row);
  fragment.append(buildTalents(slot));
  fragment.append(buildSkillsSection("our", slot, index));
  return fragment;
}

function buildOpponentFields(slot, index) {
  const fragment = document.createDocumentFragment();
  fragment.append(buildSkillsSection("opp", slot, index));
  return fragment;
}

function buildSlotCard(side, index, open) {
  const slot = getSlot(side, index);
  const active =
    side === "our" ? state.activeOur === index : state.activeOpp === index;

  const card = element("article", active ? "slot active" : "slot");
  card.id = slotElementId(side, index);
  card.dataset.side = side;
  card.dataset.index = String(index);

  const pet = slot ? meta.pets[slot.petIndex] : null;
  const head = element("div", "slot-head");
  const title = element("button", "slot-title");
  title.type = "button";
  title.append(element("span", "slot-number", String(index + 1)));
  title.append(
    thumb(pet ? pet.avatar : null, pet ? pet.name : "空位", "slot-avatar")
  );
  title.append(
    element(
      "span",
      "slot-name",
      slot ? petLabel(meta.pets[slot.petIndex]) : "空位"
    )
  );
  title.addEventListener("click", () => {
    selectActive(side, index);
  });
  head.append(title);
  head.append(
    element("span", "slot-badge", active ? "当前出战" : "点击出战")
  );
  card.append(head);

  card.append(element("div", "slot-summary", "等待计算…"));

  if (battleStarted()) {
    card.classList.add("locked");
    card.append(
      element(
        "p",
        "battle-lock-line",
        "对局已开始，初始队伍锁定；要修改请先清空对局。"
      )
    );
    return card;
  }

  const details = document.createElement("details");
  details.open = active || Boolean(open);
  details.append(element("summary", "configure-summary", "配置"));

  const body = element("div", "slot-body");
  body.append(createPetSelect(side, index, slot));
  if (slot) {
    body.append(
      side === "our" ? buildOurFields(slot, index) : buildOpponentFields(slot, index)
    );
  } else {
    body.append(element("p", "empty-note", "先选择一只精灵。"));
  }
  details.append(body);
  card.append(details);
  return card;
}

function renderTeams() {
  const ourBox = $("our-team");
  const oppBox = $("opp-team");
  const ourCards = [];
  const oppCards = [];
  for (let index = 0; index < 6; index += 1) {
    ourCards.push(buildSlotCard("our", index, false));
    oppCards.push(buildSlotCard("opp", index, false));
  }
  ourBox.replaceChildren(...ourCards);
  oppBox.replaceChildren(...oppCards);
}

function rerenderSlot(side, index, open) {
  const oldCard = document.getElementById(slotElementId(side, index));
  if (!oldCard) {
    return;
  }
  oldCard.replaceWith(buildSlotCard(side, index, open));
  refreshActiveStyles();
}

function refreshActiveStyles() {
  for (const side of ["our", "opp"]) {
    for (let index = 0; index < 6; index += 1) {
      const card = document.getElementById(slotElementId(side, index));
      if (!card) {
        continue;
      }
      const active =
        side === "our" ? state.activeOur === index : state.activeOpp === index;
      card.classList.toggle("active", active);
      const badge = card.querySelector(".slot-badge");
      if (badge) {
        badge.textContent = active ? "当前出战" : "点击出战";
      }
      if (active) {
        const details = card.querySelector("details");
        if (details) {
          details.open = true;
        }
      }
    }
  }
}

function selectActive(side, index) {
  if (side === "our") {
    state.activeOur = index;
  } else {
    state.activeOpp = index;
  }
  refreshActiveStyles();
  scheduleCalculate();
}

/* ---------- 战斗记录 ---------- */

function sideDisplay(side) {
  return side === "our" ? "我方" : "对方";
}

function slotPetLabel(side, index) {
  const slot = getSlot(side, index);
  if (!slot) {
    return "空位";
  }
  return petLabel(meta.pets[slot.petIndex]);
}

function participantText(side, index) {
  return `${sideDisplay(side)} ${slotPetLabel(side, index)}（位置 ${index + 1}）`;
}

function buffSummary(buffs) {
  const parts = [];
  for (const key of BUFF_KEYS) {
    if (buffs[key]) {
      parts.push(`${STAT_NAMES[key]}${formatBuff(buffs[key])}`);
    }
  }
  return parts.join("，");
}

function formatSigned(value) {
  if (value > 0) {
    return `+${value}`;
  }
  if (value < 0) {
    return `−${Math.abs(value)}`;
  }
  return "0";
}

function recordContextText(record) {
  if (record.type !== "damage") {
    return "";
  }
  const parts = [record.weather || "无天气"];
  const attackerBuffs = buffSummary(record.attackerBuffs);
  const defenderBuffs = buffSummary(record.defenderBuffs);
  if (attackerBuffs) {
    parts.push(`${sideDisplay(record.attackerSide)} ${attackerBuffs}`);
  }
  const defenderSide = record.attackerSide === "our" ? "opp" : "our";
  if (defenderBuffs) {
    parts.push(`${sideDisplay(defenderSide)} ${defenderBuffs}`);
  }
  if (record.starfall) {
    parts.push(`防守方星陨 ${record.starfall} 层`);
  }
  if (record.hits !== 1) {
    parts.push(`${record.hits} 连击`);
  }
  return parts.join(" · ");
}

function recordSummaryText(record) {
  if (record.type === "damage") {
    const defenderSide = record.attackerSide === "our" ? "opp" : "our";
    let text =
      `${participantText(record.attackerSide, record.attackerSlot)} → ` +
      `${participantText(defenderSide, record.defenderSlot)}` +
      ` · ${record.skill} · 伤害 ${numberText(record.damage)}`;
    if (record.attackerSide === "our" && record.hpPercent !== null) {
      text += ` · 打完剩 ${record.hpPercent}%`;
    }
    if (record.attackerSide === "opp" && record.remainingHp !== null) {
      text += ` · 我方剩 ${numberText(record.remainingHp)}`;
    }
    return text;
  }

  if (record.side === "opp") {
    let text =
      `${participantText("opp", record.slot)} · ` +
      `生命变成 ${record.hpPercent}%`;
    if (record.hpDelta !== null) {
      text += `（实际 ${formatSigned(record.hpDelta)}）`;
    }
    return text;
  }
  return `${participantText("our", record.slot)} · 生命设为 ${numberText(record.hp)}`;
}

function recordResultText(record) {
  if (record.matched === true) {
    const parts = ["对上了"];
    if (typeof record.kept === "number") {
      parts.push(`剩 ${numberText(record.kept)} 种组合`);
    }
    if (record.message) {
      parts.push(record.message);
    }
    return parts.join(" · ");
  }
  if (record.matched === false) {
    return `没对上，已跳过：${record.message || "原因不明。"}`;
  }
  return "等待计算…";
}

function recordResultClass(record) {
  if (record.matched === true) {
    return "record-result ok";
  }
  if (record.matched === false) {
    return "record-result bad";
  }
  return "record-result";
}

function markAllRecordsPending() {
  for (const record of state.records) {
    record.matched = null;
    record.kept = null;
    record.message = null;
  }
}

function setRecordStatus(text, isError = false) {
  const node = $("record-status");
  node.textContent = text || "";
  node.classList.toggle("error", Boolean(isError));
  node.hidden = !text;
}

function renderRecords() {
  const list = $("record-list");
  list.replaceChildren();
  if (!state.records.length) {
    list.append(
      element(
        "li",
        "record-empty",
        "还没有记录。打了一次之后，在上面的表格里填伤害或生命变化。"
      )
    );
    return;
  }

  state.records.forEach((record, index) => {
    const item = element("li", "record-item");
    item.dataset.id = String(record.id);

    const head = element("div", "record-item-head");
    head.append(element("span", "record-index", String(index + 1)));
    head.append(
      element(
        "span",
        "record-type-tag",
        record.type === "damage" ? "伤害" : "生命变化"
      )
    );
    head.append(element("span", "record-summary", recordSummaryText(record)));

    const remove = element("button", "record-delete", "删除");
    remove.type = "button";
    remove.addEventListener("click", () => {
      const position = state.records.findIndex(
        (candidate) => candidate.id === record.id
      );
      if (position < 0) {
        return;
      }
      state.records.splice(position, 1);
      markAllRecordsPending();
      setRecordStatus("");
      renderRecords();
      saveState();
      scheduleCalculate();
    });
    head.append(remove);
    item.append(head);

    const contextText = recordContextText(record);
    if (contextText) {
      item.append(element("div", "record-context", contextText));
    }
    item.append(
      element("div", recordResultClass(record), recordResultText(record))
    );
    list.append(item);
  });
}

function updateRecordResults(results) {
  const byIndex = new Map(
    (results || []).map((result) => [result.index, result])
  );
  state.records.forEach((record, index) => {
    const result = byIndex.get(index);
    if (!result) {
      return;
    }
    record.matched = result.matched;
    record.kept = result.kept;
    record.message = result.message || "";
  });
  renderRecords();
}

function chooseSlot(side, preferred) {
  const number = Number(preferred);
  if (
    preferred !== "" &&
    preferred !== null &&
    preferred !== undefined &&
    validSlotNumber(number) &&
    getSlot(side, number)
  ) {
    return number;
  }
  const active = side === "our" ? state.activeOur : state.activeOpp;
  if (getSlot(side, active)) {
    return active;
  }
  for (let index = 0; index < 6; index += 1) {
    if (getSlot(side, index)) {
      return index;
    }
  }
  return null;
}

function fillSlotSelect(select, side, selected) {
  const hasAny = Array.from({ length: 6 }, (_, index) =>
    getSlot(side, index)
  ).some(Boolean);
  select.replaceChildren();

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = hasAny ? "选择位置" : "没有可用位置";
  select.append(placeholder);

  for (let index = 0; index < 6; index += 1) {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = `位置 ${index + 1} · ${slotPetLabel(side, index)}`;
    if (!getSlot(side, index)) {
      option.disabled = true;
    }
    select.append(option);
  }
  select.value = selected === null ? "" : String(selected);
}

function fillRecordSkillSelect(attackerSide, attackerSlot, preferred) {
  const select = $("record-skill");
  const slot = attackerSlot === null ? null : getSlot(attackerSide, attackerSlot);
  select.replaceChildren();

  if (!slot) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "先选择攻击方位置";
    select.append(option);
    select.disabled = true;
    return;
  }

  const learnable = learnableSkills(slot.petIndex);
  if (!learnable.length) {
    const option = document.createElement("option");
    option.value = "";
    option.textContent = "这只精灵没有可学的攻击技能";
    select.append(option);
    select.disabled = true;
    return;
  }

  select.disabled = false;
  const configured = slot.skills.filter((name) => learnable.includes(name));
  const ordered = [
    ...configured,
    ...learnable.filter((name) => !configured.includes(name)),
  ];

  const placeholder = document.createElement("option");
  placeholder.value = "";
  placeholder.textContent = "选择技能";
  select.append(placeholder);

  for (const name of ordered) {
    const option = document.createElement("option");
    option.value = name;
    option.textContent =
      skillLabel(name) + (configured.includes(name) ? "（当前配置）" : "");
    select.append(option);
  }

  if (ordered.includes(preferred)) {
    select.value = preferred;
  } else if (configured.length) {
    select.value = configured[0];
  } else {
    select.value = ordered[0];
  }
}

function updateRecordForm() {
  const type = $("record-type").value;
  const isDamage = type === "damage";
  $("damage-record-fields").hidden = !isDamage;
  $("hp-change-record-fields").hidden = isDamage;
  $("add-record").textContent = isDamage ? "添加伤害记录" : "添加生命变化记录";

  if (isDamage) {
    const direction = $("record-direction").value;
    const attackerSide = direction === "our_to_opp" ? "our" : "opp";
    const defenderSide = attackerSide === "our" ? "opp" : "our";

    const attackerSelect = $("record-attacker-slot");
    const defenderSelect = $("record-defender-slot");
    const attackerSlot = chooseSlot(attackerSide, attackerSelect.value);
    const defenderSlot = chooseSlot(defenderSide, defenderSelect.value);
    fillSlotSelect(attackerSelect, attackerSide, attackerSlot);
    fillSlotSelect(defenderSelect, defenderSide, defenderSlot);

    fillRecordSkillSelect(
      attackerSide,
      attackerSlot,
      $("record-skill").value
    );

    $("record-hp-percent-field").hidden = attackerSide !== "our";
    $("record-remaining-hp-field").hidden = attackerSide === "our";
    return;
  }

  const side = $("record-hp-side").value;
  const slotSelect = $("record-hp-slot");
  const slot = chooseSlot(side, slotSelect.value);
  fillSlotSelect(slotSelect, side, slot);
  $("record-opp-hp-fields").hidden = side !== "opp";
  $("record-our-hp-field").hidden = side !== "our";
}

function initRecordForm() {
  $("record-type").addEventListener("change", updateRecordForm);
  $("record-direction").addEventListener("change", updateRecordForm);
  $("record-attacker-slot").addEventListener("change", updateRecordForm);
  $("record-defender-slot").addEventListener("change", updateRecordForm);
  $("record-hp-side").addEventListener("change", updateRecordForm);
  $("record-hp-slot").addEventListener("change", updateRecordForm);
  $("add-record").addEventListener("click", addRecord);
  updateRecordForm();
}

function optionalInteger(input, label, min, max) {
  const raw = input.value.trim();
  if (raw === "") {
    return null;
  }
  const value = Number(raw);
  if (!Number.isInteger(value) || value < min || value > max) {
    setRecordStatus(`${label}必须是 ${min}～${max} 的整数。`, true);
    return NaN;
  }
  return value;
}

function requiredInteger(input, label, min, max) {
  if (input.value.trim() === "") {
    setRecordStatus(`${label}不能为空。`, true);
    return NaN;
  }
  const value = Number(input.value);
  if (!Number.isInteger(value) || value < min || value > max) {
    setRecordStatus(`${label}必须是 ${min}～${max} 的整数。`, true);
    return NaN;
  }
  return value;
}

function addDamageRecord() {
  const damageRaw = $("record-damage").value.trim();
  const damage = Number(damageRaw);
  if (damageRaw === "" || !Number.isInteger(damage) || damage < 0) {
    setRecordStatus("伤害必须是 0 或更大的整数。", true);
    return;
  }

  const direction = $("record-direction").value;
  const attackerSide = direction === "our_to_opp" ? "our" : "opp";
  const defenderSide = attackerSide === "our" ? "opp" : "our";
  const attackerRaw = $("record-attacker-slot").value;
  const defenderRaw = $("record-defender-slot").value;
  const attackerSlot = Number(attackerRaw);
  const defenderSlot = Number(defenderRaw);
  if (
    attackerRaw === "" ||
    !validSlotNumber(attackerSlot) ||
    !getSlot(attackerSide, attackerSlot)
  ) {
    setRecordStatus("请选择一个有精灵的攻击方位置。", true);
    return;
  }
  if (
    defenderRaw === "" ||
    !validSlotNumber(defenderSlot) ||
    !getSlot(defenderSide, defenderSlot)
  ) {
    setRecordStatus("请选择一个有精灵的防守方位置。", true);
    return;
  }

  const skill = $("record-skill").value;
  if (!skill) {
    setRecordStatus("请选择这次使用的技能。", true);
    return;
  }

  const attacker = getSlot(attackerSide, attackerSlot);
  if (!learnableSkills(attacker.petIndex).includes(skill)) {
    setRecordStatus("这个技能不在该精灵能学的攻击技能里。", true);
    return;
  }

  let hpPercent = null;
  let remainingHp = null;
  if (attackerSide === "our") {
    hpPercent = optionalInteger($("record-hp-percent"), "对方剩余生命百分比", 0, 100);
    if (Number.isNaN(hpPercent)) {
      return;
    }
  } else {
    remainingHp = optionalInteger($("record-remaining-hp"), "我方剩余生命", 0, 999999);
    if (Number.isNaN(remainingHp)) {
      return;
    }
  }

  const hits = requiredInteger($("record-hits"), "连击次数", 1, 99);
  if (Number.isNaN(hits)) {
    return;
  }
  const starfall = requiredInteger($("record-starfall"), "星陨层数", 0, 99);
  if (Number.isNaN(starfall)) {
    return;
  }

  const defender = getSlot(defenderSide, defenderSlot);
  state.records.push({
    id: state.nextRecordId,
    type: "damage",
    attackerSide,
    attackerSlot,
    defenderSlot,
    skill,
    damage,
    hpPercent,
    remainingHp,
    weather: state.weather || "",
    attackerBuffs: { ...attacker.buffs },
    defenderBuffs: { ...defender.buffs },
    starfall,
    hits,
    matched: null,
    kept: null,
    message: null,
  });
  state.nextRecordId += 1;

  $("record-damage").value = "";
  $("record-hp-percent").value = "";
  $("record-remaining-hp").value = "";
  $("record-hits").value = "1";
  $("record-starfall").value = "0";
  setRecordStatus("");
  markAllRecordsPending();
  renderRecords();
  saveState();
  scheduleCalculate();
}

function addHpChangeRecord() {
  const side = $("record-hp-side").value;
  const slotRaw = $("record-hp-slot").value;
  const slot = Number(slotRaw);
  if (slotRaw === "" || !validSlotNumber(slot) || !getSlot(side, slot)) {
    setRecordStatus("请选择一个有精灵的位置。", true);
    return;
  }

  if (side === "opp") {
    const percentRaw = $("record-hp-percent-change").value.trim();
    const percent = Number(percentRaw);
    if (
      percentRaw === "" ||
      !Number.isInteger(percent) ||
      percent < 0 ||
      percent > 100
    ) {
      setRecordStatus("对方剩余生命百分比必须是 0～100 的整数。", true);
      return;
    }

    let delta = null;
    const deltaRaw = $("record-hp-delta").value.trim();
    if (deltaRaw !== "") {
      delta = Number(deltaRaw);
      if (!Number.isInteger(delta)) {
        setRecordStatus("实际回血/掉血必须是整数，回血填正数，掉血填负数。", true);
        return;
      }
    }

    state.records.push({
      id: state.nextRecordId,
      type: "hp_change",
      side: "opp",
      slot,
      hpPercent: percent,
      hpDelta: delta,
      matched: null,
      kept: null,
      message: null,
    });
    $("record-hp-percent-change").value = "";
    $("record-hp-delta").value = "";
  } else {
    const hpRaw = $("record-our-hp").value.trim();
    const hp = Number(hpRaw);
    if (hpRaw === "" || !Number.isInteger(hp) || hp < 0) {
      setRecordStatus("我方生命必须是 0 或更大的整数。", true);
      return;
    }
    state.records.push({
      id: state.nextRecordId,
      type: "hp_change",
      side: "our",
      slot,
      hp,
      matched: null,
      kept: null,
      message: null,
    });
    $("record-our-hp").value = "";
  }

  state.nextRecordId += 1;
  setRecordStatus("");
  markAllRecordsPending();
  renderRecords();
  saveState();
  scheduleCalculate();
}

function addRecord() {
  if ($("record-type").value === "damage") {
    addDamageRecord();
  } else {
    addHpChangeRecord();
  }
}

function updateSlotSummaries(data) {
  for (const side of ["our", "opp"]) {
    const list = side === "our" ? data.our_team : data.opponent_team;
    for (let index = 0; index < 6; index += 1) {
      const card = document.getElementById(slotElementId(side, index));
      if (!card) {
        continue;
      }
      const box = card.querySelector(".slot-summary");
      if (!box) {
        continue;
      }
      box.replaceChildren();
      const entry = list[index];
      if (!entry || !entry.configured) {
        box.append(element("span", "empty-note", "未选择精灵"));
        continue;
      }

      if (side === "our") {
        box.append(
          element(
            "div",
            "summary-line summary-strong",
            `生命 ${rangeText(entry.current_hp)} / ${numberText(entry.max_hp)}`
          )
        );
        if (entry.energy !== undefined && entry.energy !== null) {
          box.append(
            element(
              "div",
              "summary-line",
              `能量 ${numberText(entry.energy)} / ${numberText(entry.max_energy)}`
            )
          );
        }
        box.append(
          element(
            "div",
            "summary-line summary-stats",
            STAT_ORDER.map(
              (key) => `${STAT_NAMES[key]} ${numberText(entry.stats[key])}`
            ).join(" · ")
          )
        );
      } else {
        const builds = entry.builds || {};
        box.append(
          element(
            "div",
            "summary-line summary-strong",
            `剩余 ${rangeText(entry.current_hp_percent, "%")} · 生命 ${rangeText(entry.current_hp)}`
          )
        );
        if (entry.energy !== undefined && entry.energy !== null) {
          box.append(
            element(
              "div",
              "summary-line",
              `能量 ${numberText(entry.energy)} / ${numberText(entry.max_energy)}`
            )
          );
        }
        box.append(
          element(
            "div",
            "summary-line",
            `可能 ${numberText(builds.remaining)} / ${numberText(builds.total)} 种 · ${numberText(entry.natures_remaining)} 种性格`
          )
        );
        box.append(
          element(
            "div",
            "summary-line summary-stats",
            STAT_ORDER.map(
              (key) => `${STAT_NAMES[key]} ${rangeText(entry.stats[key])}`
            ).join(" · ")
          )
        );
        if (
          Array.isArray(entry.observed_skills) &&
          entry.observed_skills.length
        ) {
          box.append(
            element(
              "div",
              "summary-line summary-observed",
              `记录中见过：${entry.observed_skills.join("、")}`
            )
          );
        }
      }
      if (Array.isArray(entry.statuses) && entry.statuses.length) {
        box.append(
          element(
            "div",
            "summary-line summary-statuses",
            `状态：${entry.statuses
              .map((status) => `${status.name} ${status.layers} 层`)
              .join("、")}`
          )
        );
      }
    }
  }
}

function updateHpHints(data) {
  for (let index = 0; index < 6; index += 1) {
    const card = document.getElementById(slotElementId("our", index));
    if (!card) {
      continue;
    }
    const input = card.querySelector(".hp-input");
    const entry = data.our_team[index];
    if (!input || !entry || !entry.configured) {
      continue;
    }
    input.max = String(entry.max_hp);
    input.placeholder = `留空 = 满血 ${entry.max_hp}`;
  }
}

function buildActiveSide(side, data, slotIndex) {
  const box = element("div", `active-side ${side}`);
  box.append(element("span", "active-tag", side === "our" ? "我方" : "对方"));
  const localSlot =
    side === "our" ? state.ourTeam[slotIndex] : state.oppTeam[slotIndex];
  const pet = localSlot ? meta.pets[localSlot.petIndex] : null;
  box.append(
    thumb(
      pet ? pet.avatar : null,
      pet ? pet.name : summaryLabel(data),
      "active-avatar"
    )
  );

  const body = element("div", "active-side-body");
  body.append(element("div", "active-pet", summaryLabel(data)));
  if (side === "our") {
    body.append(
      element(
        "div",
        "active-meta",
        `生命 ${rangeText(data.current_hp)} / ${numberText(data.max_hp)}`
      )
    );
    body.append(
      element(
        "div",
        "active-meta",
        `速度 ${numberText(data.speed_after_buffs)}`
      )
    );
  } else {
    const builds = data.builds || {};
    body.append(
      element(
        "div",
        "active-meta",
        `生命 ${rangeText(data.current_hp)} · 显示 ${rangeText(data.current_hp_percent, "%")}`
      )
    );
    body.append(
      element(
        "div",
        "active-meta",
        `速度 ${rangeText(data.speed_after_buffs)}`
      )
    );
    body.append(
      element(
        "div",
        "active-meta",
        `可能 ${numberText(builds.remaining)} / ${numberText(builds.total)} 种`
      )
    );
  }
  box.append(body);
  return box;
}

function speedNoteText(our, opponent) {
  const ourSpeed = our && our.speed_after_buffs;
  const range = opponent && opponent.speed_after_buffs;
  if (
    typeof ourSpeed !== "number" ||
    !range ||
    typeof range.min !== "number" ||
    typeof range.max !== "number"
  ) {
    return "";
  }
  let conclusion;
  if (ourSpeed > range.max) {
    conclusion = "同先手值时，我方一定先手";
  } else if (ourSpeed < range.min) {
    conclusion = "同先手值时，对方一定先手";
  } else {
    conclusion =
      `同先手值时，对方速度低于 ${numberText(ourSpeed)} 时我方先手，` +
      `高于 ${numberText(ourSpeed)} 时对方先手，` +
      `等于 ${numberText(ourSpeed)} 时同速顺序不确定`;
  }
  return (
    `速度判断：我方 ${numberText(ourSpeed)}，对方 ${rangeText(range)}。` +
    `先比先手值，再比速度；${conclusion}。`
  );
}

function buildSkillRow(skill, side) {
  const row = element("div", skill.available ? "skill-row" : "skill-row unavailable");

  const info = skillByName.get(skill.skill);
  const head = element("div", "skill-row-head");
  head.append(thumb(info ? info.icon : null, skill.skill, "skill-row-icon"));
  head.append(element("span", "skill-name-text", skill.skill));
  const power = skill.power === null || skill.power === undefined
    ? "?"
    : skill.power;
  head.append(
    element(
      "span",
      "skill-tag",
      `${skill.element} · ${skill.damage_class} · 威力 ${power}`
    )
  );
  if (info && info.priority) {
    head.append(
      element(
        "span",
        "priority-tag",
        `先手 ${formatSigned(info.priority)}`
      )
    );
  } else if (info && info.priority_note) {
    head.append(
      element("span", "priority-tag conditional", `条件先手：${info.priority_note}`)
    );
  }
  row.append(head);

  if (!skill.available) {
    row.append(element("p", "skill-reason", skill.reason || "无法计算。"));
    return row;
  }

  const metrics = element("div", "skill-metrics");
  metrics.append(element("span", "damage-value", `伤害 ${rangeText(skill.damage)}`));

  let remaining;
  if (side === "our") {
    remaining = `对方剩余 ${rangeText(skill.remaining_hp_percent, "%")}`;
  } else {
    remaining = `我方剩余 ${rangeText(skill.our_remaining_hp)}`;
  }
  metrics.append(element("span", "remain-value", remaining));
  row.append(metrics);

  const knockout = skill.knockout || "impossible";
  row.append(
    element(
      "span",
      `ko-tag ${knockout}`,
      KNOCKOUT_TEXT[knockout] || "无法判断"
    )
  );
  return row;
}

function buildSkillPanel(title, skills, side) {
  const panel = element("section", "skill-panel");
  panel.append(element("h3", "panel-title", title));

  if (!skills || !skills.length) {
    panel.append(element("p", "empty-note", "这个位置还没有技能。"));
    return panel;
  }

  for (const skill of skills) {
    panel.append(buildSkillRow(skill, side));
  }
  return panel;
}

function renderActive(active, data) {
  const head = $("active-head");
  const tables = $("active-tables");
  head.replaceChildren();
  tables.replaceChildren();

  if (!active) {
    head.append(
      element("p", "empty-note", "当前出战位置上还有空位，请在我方和对方各选一个位置。")
    );
    tables.append(element("p", "empty-note", "选定双方位置后，这里会列出技能伤害表。"));
    return;
  }

  const our = data.our_team[active.our_slot];
  const opponent = data.opponent_team[active.opponent_slot];
  head.append(buildActiveSide("our", our, active.our_slot));
  head.append(element("span", "vs", "对"));
  head.append(buildActiveSide("opp", opponent, active.opponent_slot));
  const speedNote = speedNoteText(our, opponent);
  if (speedNote) {
    head.append(element("div", "speed-note", speedNote));
  }

  tables.append(
    buildSkillPanel("我方技能 → 对方", active.our_skills, "our")
  );
  tables.append(
    buildSkillPanel("对方技能 → 我方", active.opponent_skills, "opp")
  );
}

const BATTLE_POSITIVE_MARKS = [
  "攻击印记", "光合印记", "湿润印记", "蓄电印记",
  "风起印记", "蓄势印记", "龙噬印记", "萌芽印记",
];
const BATTLE_NEGATIVE_MARKS = [
  "中毒印记", "棘刺印记", "降灵印记", "减速印记",
  "星陨印记", "暗涌印记",
];
const BATTLE_TEMP_STATUSES = ["中毒", "灼烧", "寄生", "禁足"];
const BATTLE_PERM_STATUSES = ["冻结", "萌化", "引电"];
const BATTLE_WEATHER_OPTIONS = ["雨天", "沙暴", "暴风雪", "雷鸣", "霞光梦境"];
const BATTLE_HP_REASONS = [
  ["heal", "回血"],
  ["recoil", "反伤/自损"],
  ["weather", "天气伤害"],
  ["status", "状态伤害"],
  ["starfall", "星陨伤害"],
  ["switch_effect", "换人/印记效果"],
  ["other", "其他"],
];

function setBattleStatus(text, isError = false) {
  const node = $("battle-status");
  if (!node) {
    return;
  }
  node.textContent = text || "";
  node.classList.toggle("error", Boolean(isError));
  node.hidden = !text;
}

function battleSideText(side) {
  return side === "our" ? "我方" : "对方";
}

function battleActionText(action) {
  if (!action) {
    return "空";
  }
  if (action.type === "switch") {
    return `切换到第 ${action.to_slot + 1} 只`;
  }
  if (action.type === "charge") {
    return "聚能";
  }
  return `使用 ${action.skill}`;
}

function battleStatusListText(statuses) {
  if (!Array.isArray(statuses) || !statuses.length) {
    return "";
  }
  return statuses.map((status) => `${status.name}:${status.layers}`).join(",");
}

function parseBattleStatusText(text, label) {
  const result = [];
  const seen = new Set();
  for (const part of String(text || "").split(/[，,]/)) {
    const item = part.trim();
    if (!item) {
      continue;
    }
    const match = item.match(/^([^:：]+)[:：](\d+)$/);
    if (!match) {
      throw new Error(`${label}的状态要写成“中毒:2,灼烧:1”这种格式。`);
    }
    const name = match[1].trim();
    const layers = Number(match[2]);
    if (!BATTLE_TEMP_STATUSES.includes(name) && !BATTLE_PERM_STATUSES.includes(name)) {
      throw new Error(`${label}的状态“${name}”不认识。`);
    }
    if (seen.has(name)) {
      throw new Error(`${label}的状态“${name}”重复了。`);
    }
    seen.add(name);
    if (layers > 0) {
      result.push({ name, layers });
    }
  }
  return result;
}

function battleSideEntries(snapshot, side) {
  return side === "our" ? snapshot.our_pets : snapshot.opp_pets;
}

function battleEntry(snapshot, side, slot) {
  return battleSideEntries(snapshot, side)[slot] || null;
}

function battlePetName(side, slot) {
  const team = side === "our" ? state.ourTeam : state.oppTeam;
  const slotData = team[slot];
  if (!slotData) {
    return `第 ${slot + 1} 只`;
  }
  return petLabel(meta.pets[slotData.petIndex]);
}

function battlePetMaxHp(side, slot) {
  if (side === "our") {
    const entry = battleDraft && battleDraft.our_pets[slot];
    if (entry && entry.max_hp) {
      return entry.max_hp;
    }
    if (
      latestTeamResult &&
      latestTeamResult.our_team &&
      latestTeamResult.our_team[slot] &&
      latestTeamResult.our_team[slot].max_hp
    ) {
      return latestTeamResult.our_team[slot].max_hp;
    }
  }
  return null;
}

function battleTeamSignature() {
  const ours = state.ourTeam.map((slot) =>
    slot
      ? [slot.petIndex, slot.nature, slot.talents, slot.skills]
      : null
  );
  const opps = state.oppTeam.map((slot) =>
    slot ? [slot.petIndex, slot.skills] : null
  );
  const maxHp = latestTeamResult
    ? latestTeamResult.our_team.map((entry) =>
        entry && entry.configured ? entry.max_hp : null
      )
    : null;
  return JSON.stringify({
    ours,
    opps,
    activeOur: state.activeOur,
    activeOpp: state.activeOpp,
    maxHp,
  });
}

function battlePreSnapshot() {
  if (battleStarted()) {
    return currentBattleSnapshot();
  }
  return battlePreBase;
}

function battlePreSlots() {
  const snapshot = battlePreSnapshot();
  if (!snapshot) {
    return null;
  }
  return {
    snapshot,
    our: snapshot.active_our,
    opp: snapshot.active_opp,
  };
}

function battleEntryForContext(snapshot, side, slot) {
  const entry = battleEntry(snapshot, side, slot);
  if (!entry) {
    return null;
  }
  return {
    slot,
    hp: side === "our" ? entry.hp : undefined,
    hp_percent: side === "opp" ? entry.hp_percent : undefined,
    energy: entry.energy,
    max_energy: entry.max_energy,
    buffs: clone(entry.buffs || zeroBuffSet()),
    statuses: clone(entry.statuses || []),
  };
}

function battleContextFromSnapshot(snapshot, sourceSide, sourceSlot, targetSide, targetSlot) {
  const ourSlot = sourceSide === "our" ? sourceSlot : targetSlot;
  const oppSlot = sourceSide === "theirs" ? sourceSlot : targetSlot;
  return {
    weather: snapshot.weather ? clone(snapshot.weather) : null,
    our_marks: clone(snapshot.our_marks || { positive: null, negative: null }),
    opp_marks: clone(snapshot.opp_marks || { positive: null, negative: null }),
    our_active: battleEntryForContext(snapshot, "our", ourSlot),
    opp_active: battleEntryForContext(snapshot, "opp", oppSlot),
  };
}

function battleActionCategory(skillName) {
  const info = actionSkillByName.get(skillName);
  return info ? info.category : null;
}

function battleActionInfo(skillName) {
  return actionSkillByName.get(skillName) || null;
}

function battleTurnActiveSlots() {
  const pre = battlePreSlots();
  if (!pre) {
    return null;
  }
  let our = pre.our;
  let opp = pre.opp;
  if ($("battle-our-action-type").value === "switch") {
    our = Number($("battle-our-switch").value);
  }
  if ($("battle-opp-action-type").value === "switch") {
    opp = Number($("battle-opp-switch").value);
  }
  return { our, opp, pre };
}

function syncBattleDamageEvents() {
  const slots = battleTurnActiveSlots();
  if (!slots) {
    battleDraftDamageEvents = [];
    return;
  }
  const previous = new Map(
    battleDraftDamageEvents.map((event) => [
      `${event.sourceSide}:${event.skill}`,
      event,
    ])
  );
  const next = [];
  const ourType = $("battle-our-action-type").value;
  const oppType = $("battle-opp-action-type").value;
  if (ourType === "skill") {
    const skill = $("battle-our-skill").value;
    const info = battleActionInfo(skill);
    if (info && info.category === "攻击") {
      const old = previous.get(`our:${skill}`);
      next.push(old || {
        sourceSide: "our",
        sourceSlot: slots.our,
        targetSide: "theirs",
        targetSlot: slots.opp,
        skill,
        totalDamage: "",
        hits: 1,
        hpAfter: "",
        overkill: false,
        ignore: false,
      });
    }
  }
  if (oppType === "skill") {
    const skill = $("battle-opp-skill").value;
    const info = battleActionInfo(skill);
    if (info && info.category === "攻击") {
      const old = previous.get(`theirs:${skill}`);
      next.push(old || {
        sourceSide: "theirs",
        sourceSlot: slots.opp,
        targetSide: "our",
        targetSlot: slots.our,
        skill,
        totalDamage: "",
        hits: 1,
        hpAfter: "",
        overkill: false,
        ignore: false,
      });
    }
  }
  battleDraftDamageEvents = next;
}

function resetBattleDraft(snapshot) {
  battleDraft = clone(snapshot);
  battlePreBase = clone(snapshot);
  battleDraftDamageEvents = [];
  battleDraftHpEvents = [];
  battleDraftReplacements = { our: null, opp: null };
  battleStatusText = {};
  for (const side of ["our", "opp"]) {
    const entries = battleSideEntries(battleDraft, side);
    for (let slot = 0; slot < 6; slot += 1) {
      if (entries[slot]) {
        battleStatusText[`${side}-${slot}`] = battleStatusListText(
          entries[slot].statuses
        );
      }
    }
  }
  battleDraftSig = battleTeamSignature();
  syncBattleDamageEvents();
}

function ensureBattleDraft() {
  if (battleStarted()) {
    if (!battleDraft) {
      const snapshot = battlePreSnapshot();
      if (snapshot) {
        resetBattleDraft(snapshot);
      }
    }
    return battleDraft;
  }
  const signature = battleTeamSignature();
  if (battleDraft && battleDraftSig === signature) {
    return battleDraft;
  }
  try {
    const initial = initialFromTeamConfig();
    const snapshot = initialSnapshotFromInitial(initial);
    resetBattleDraft(snapshot);
  } catch (error) {
    battleDraft = null;
  }
  return battleDraft;
}

function populateSelect(select, options, selected) {
  select.replaceChildren();
  for (const [value, label] of options) {
    const option = document.createElement("option");
    option.value = String(value);
    option.textContent = label;
    select.append(option);
  }
  if (selected !== undefined && selected !== null) {
    select.value = String(selected);
  }
}

function configuredSlots(side) {
  const team = side === "our" ? state.ourTeam : state.oppTeam;
  return team
    .map((slot, index) => (slot ? index : null))
    .filter((index) => index !== null);
}

function slotOptions(side, includeNone = false) {
  const options = includeNone ? [["", "不补位"]] : [];
  for (const slot of configuredSlots(side)) {
    options.push([slot, `第 ${slot + 1} 只 · ${battlePetName(side, slot)}`]);
  }
  return options;
}

function aliveBenchOptions(side, activeSlot, snapshot) {
  const entries = battleSideEntries(snapshot, side);
  const options = [];
  for (const slot of configuredSlots(side)) {
    if (slot === activeSlot) {
      continue;
    }
    const entry = entries[slot];
    if (!entry) {
      continue;
    }
    const hp = side === "our" ? entry.hp : entry.hp_percent;
    if (hp > 0) {
      options.push([slot, `第 ${slot + 1} 只 · ${battlePetName(side, slot)}`]);
    }
  }
  return options;
}

function battleOppSkillOptions(activeSlot) {
  const names = new Set();
  const slot = state.oppTeam[activeSlot];
  if (slot) {
    for (const name of slot.skills) {
      names.add(name);
    }
  }
  const inference = state.battle.inference;
  if (inference && inference.opponent_team && inference.opponent_team[activeSlot]) {
    for (const name of inference.opponent_team[activeSlot].observed_skills || []) {
      names.add(name);
    }
  }
  if (
    slot &&
    meta.pets[slot.petIndex] &&
    Array.isArray(meta.pets[slot.petIndex].action_skills)
  ) {
    for (const name of meta.pets[slot.petIndex].action_skills) {
      names.add(name);
    }
  }
  if ($("battle-show-all-opp-skills").checked) {
    for (const skill of meta.action_skills) {
      names.add(skill.name);
    }
  }
  return [...names].sort((a, b) => a.localeCompare(b, "zh-CN"));
}

function renderBattleCurrent(pre) {
  const box = $("battle-current");
  box.replaceChildren();
  if (!pre) {
    box.append(element("p", "empty-note", "先在上方配置双方首发精灵。"));
    return;
  }
  for (const side of ["our", "opp"]) {
    const slot = side === "our" ? pre.our : pre.opp;
    const entry = battleEntry(pre.snapshot, side, slot);
    const line = element("div", `battle-current-side ${side}`);
    const team = side === "our" ? state.ourTeam : state.oppTeam;
    const pet = team[slot] ? meta.pets[team[slot].petIndex] : null;
    if (pet) {
      line.append(thumb(pet.avatar, pet.name, "battle-current-avatar"));
    }
    line.append(
      element(
        "strong",
        "",
        `${side === "our" ? "我方" : "对方"}：${battlePetName(side, slot)}`
      )
    );
    if (entry) {
      const hpText =
        side === "our"
          ? `生命 ${numberText(entry.hp)} / ${numberText(entry.max_hp)}`
          : `剩余 ${numberText(entry.hp_percent)}%`;
      line.append(
        element(
          "span",
          "battle-current-meta",
          `${hpText} · 能量 ${numberText(entry.energy)} / ${numberText(entry.max_energy)}`
        )
      );
    }
    box.append(line);
  }
}

function renderBattleActionSelectors(pre) {
  if (!pre) {
    return;
  }
  const ourSlot = pre.our;
  const oppSlot = pre.opp;
  const ourSlotData = state.ourTeam[ourSlot];
  const ourSkillNames = ourSlotData ? ourSlotData.skills : [];
  const ourOptions = [["", "— 选择技能 —"]].concat(
    ourSkillNames.map((name) => [name, skillLabel(name)])
  );
  const currentOurSkill = $("battle-our-skill").value;
  populateSelect(
    $("battle-our-skill"),
    ourOptions,
    ourSkillNames.includes(currentOurSkill) ? currentOurSkill : ""
  );

  const oppNames = battleOppSkillOptions(oppSlot);
  const currentOppSkill = $("battle-opp-skill").value;
  const oppOptions = [["", "— 选择技能 —"]].concat(
    oppNames.map((name) => [name, skillLabel(name)])
  );
  populateSelect(
    $("battle-opp-skill"),
    oppOptions,
    oppNames.includes(currentOppSkill) ? currentOppSkill : ""
  );

  const ourSwitchOptions = [["", "— 选择切换目标 —"]].concat(
    aliveBenchOptions("our", ourSlot, pre.snapshot)
  );
  const currentOurSwitch = $("battle-our-switch").value;
  populateSelect(
    $("battle-our-switch"),
    ourSwitchOptions,
    ourSwitchOptions.some(([value]) => String(value) === currentOurSwitch)
      ? currentOurSwitch
      : ""
  );

  const oppSwitchOptions = [["", "— 选择切换目标 —"]].concat(
    aliveBenchOptions("opp", oppSlot, pre.snapshot)
  );
  const currentOppSwitch = $("battle-opp-switch").value;
  populateSelect(
    $("battle-opp-switch"),
    oppSwitchOptions,
    oppSwitchOptions.some(([value]) => String(value) === currentOppSwitch)
      ? currentOppSwitch
      : ""
  );

  const ourIntended = [["", "— 不确定 —"]].concat(
    ourSkillNames.map((name) => [name, skillLabel(name)])
  );
  const currentOurIntended = $("battle-our-intended-skill").value;
  populateSelect(
    $("battle-our-intended-skill"),
    ourIntended,
    ourSkillNames.includes(currentOurIntended) ? currentOurIntended : ""
  );

  const oppIntended = [["", "— 不确定 —"]].concat(
    oppNames.map((name) => [name, skillLabel(name)])
  );
  const currentOppIntended = $("battle-opp-intended-skill").value;
  populateSelect(
    $("battle-opp-intended-skill"),
    oppIntended,
    oppNames.includes(currentOppIntended) ? currentOppIntended : ""
  );

  const ourType = $("battle-our-action-type").value;
  const oppType = $("battle-opp-action-type").value;
  $("battle-our-skill-field").hidden = ourType !== "skill";
  $("battle-our-switch-field").hidden = ourType !== "switch";
  $("battle-our-intended-field").hidden = ourType !== "none";
  $("battle-opp-skill-field").hidden = oppType !== "skill";
  $("battle-opp-switch-field").hidden = oppType !== "switch";
  $("battle-opp-intended-field").hidden = oppType !== "none";
}

function battleResultText(result) {
  if (!result || !result.winner) {
    return "";
  }
  const winner =
    result.winner === "our" ? "我方胜利" : result.winner === "opp" ? "对方胜利" : "平局";
  const reasonMap = {
    magic: "魔力值耗尽",
    no_bench: "没有可上场精灵",
    surrender: "投降/断线",
  };
  const reason = result.reason ? reasonMap[result.reason] || result.reason : "";
  return reason ? `对局结束：${winner}（${reason}）` : `对局结束：${winner}`;
}

function syncHeaderWeather() {
  const select = $("weather");
  if (!select) {
    return;
  }
  if (battleStarted()) {
    const snapshot = currentBattleSnapshot();
    const type = snapshot && snapshot.weather ? snapshot.weather.type : "";
    select.value = type === "雨天" ? "雨天" : "";
    select.disabled = true;
  } else {
    select.disabled = false;
    select.value = state.weather;
  }
}

function renderBattleCard() {
  syncHeaderWeather();
  const resultBox = $("battle-result");
  if (resultBox) {
    const text = battleResultText(
      (state.battle.inference && state.battle.inference.result) ||
        state.battle.result
    );
    resultBox.textContent = text;
    resultBox.hidden = !text;
  }
  const lock = $("battle-lock-note");
  if (lock) {
    if (battleStarted()) {
      lock.hidden = false;
      lock.textContent =
        "对局已开始：初始队伍锁定。要修改队伍，请先点“清空对局”。";
    } else {
      lock.hidden = true;
    }
  }
  const draft = ensureBattleDraft();
  const pre = battlePreSlots();
  renderBattleCurrent(pre);
  renderBattleActionSelectors(pre);
  if (!draft || !pre) {
    $("battle-damage-events").replaceChildren(
      element("p", "empty-note", "先在上方配置双方首发精灵。")
    );
    $("battle-hp-events").replaceChildren();
    $("battle-snapshot-editor").replaceChildren(
      element("p", "empty-note", "先配置队伍。")
    );
    $("battle-turn-list").replaceChildren();
    return;
  }
  syncBattleDamageEvents();
  renderBattleDamageEvents();
  renderBattleHpEvents();
  renderBattleSnapshotEditor();
  renderBattleTurnList();
  renderBattleWarnings();
}

function renderBattleDamageEvents() {
  const box = $("battle-damage-events");
  box.replaceChildren();
  if (!battleDraftDamageEvents.length) {
    box.append(
      element(
        "p",
        "empty-note",
        "本回合没有攻击技能伤害。防御/状态技能造成的伤害可以加到“生命变化事件”。"
      )
    );
    return;
  }
  battleDraftDamageEvents.forEach((event, index) => {
    const card = element("div", "battle-event-card");
    const title = element(
      "div",
      "battle-event-title",
      `${battleSideText(event.sourceSide)} ${battlePetName(
        event.sourceSide,
        event.sourceSlot
      )} 使用 ${event.skill} → ${battleSideText(
        event.targetSide
      )} ${battlePetName(event.targetSide, event.targetSlot)}`
    );
    card.append(title);

    const row = element("div", "record-field-pair");
    const damage = document.createElement("input");
    damage.type = "number";
    damage.min = "0";
    damage.step = "1";
    damage.placeholder = "总伤害";
    damage.value = event.totalDamage;
    damage.addEventListener("input", () => {
      event.totalDamage = damage.value;
    });
    row.append(field("总伤害", damage));

    const hits = document.createElement("input");
    hits.type = "number";
    hits.min = "1";
    hits.max = "99";
    hits.step = "1";
    hits.value = event.hits;
    hits.addEventListener("input", () => {
      event.hits = hits.value;
    });
    row.append(field("连击数", hits));

    const hpAfter = document.createElement("input");
    hpAfter.type = "number";
    hpAfter.min = "0";
    hpAfter.max = event.targetSide === "theirs" ? "100" : "99999";
    hpAfter.step = "1";
    hpAfter.placeholder =
      event.targetSide === "theirs" ? "对方剩（%）" : "我方剩（具体生命）";
    hpAfter.value = event.hpAfter;
    hpAfter.addEventListener("input", () => {
      event.hpAfter = hpAfter.value;
    });
    row.append(
      field(
        event.targetSide === "theirs" ? "对方行动后剩（%）" : "我方行动后剩",
        hpAfter
      )
    );
    card.append(row);

    const options = element("div", "battle-event-options");
    const overkill = document.createElement("input");
    overkill.type = "checkbox";
    overkill.checked = event.overkill;
    overkill.addEventListener("change", () => {
      event.overkill = overkill.checked;
    });
    options.append(
      field("伤害超过剩余生命（溢出）", overkill)
    );

    const ignore = document.createElement("input");
    ignore.type = "checkbox";
    ignore.checked = event.ignore;
    ignore.addEventListener("change", () => {
      event.ignore = ignore.checked;
    });
    options.append(
      field("不参与推断（状态复杂/未建模）", ignore)
    );
    card.append(options);
    box.append(card);
  });
}

function hpEventTargetOptions(side) {
  return slotOptions(side).map(([value, label]) => [String(value), label]);
}

function renderBattleHpEvents() {
  const box = $("battle-hp-events");
  box.replaceChildren();
  if (!battleDraftHpEvents.length) {
    box.append(
      element("p", "empty-note", "没有回血、掉血、天气或状态伤害时可以留空。")
    );
    return;
  }
  battleDraftHpEvents.forEach((event, index) => {
    const card = element("div", "battle-event-card");
    const row = element("div", "record-field-pair");
    const sideSelect = document.createElement("select");
    populateSelect(
      sideSelect,
      [["our", "我方"], ["theirs", "对方"]],
      event.targetSide
    );
    sideSelect.addEventListener("change", () => {
      event.targetSide = sideSelect.value;
      event.targetSlot = configuredSlots(
        event.targetSide === "our" ? "our" : "opp"
      )[0] ?? 0;
      renderBattleHpEvents();
    });
    row.append(field("哪一方", sideSelect));

    const slotSelect = document.createElement("select");
    populateSelect(
      slotSelect,
      hpEventTargetOptions(event.targetSide === "our" ? "our" : "opp"),
      event.targetSlot
    );
    slotSelect.addEventListener("change", () => {
      event.targetSlot = Number(slotSelect.value);
    });
    row.append(field("哪只精灵", slotSelect));
    card.append(row);

    const second = element("div", "record-field-pair");
    const reasonSelect = document.createElement("select");
    populateSelect(reasonSelect, BATTLE_HP_REASONS, event.reason);
    reasonSelect.addEventListener("change", () => {
      event.reason = reasonSelect.value;
    });
    second.append(field("原因", reasonSelect));

    const amount = document.createElement("input");
    amount.type = "number";
    amount.step = "1";
    amount.placeholder = "回血填正数，掉血填负数；可不填";
    amount.value = event.amount;
    amount.addEventListener("input", () => {
      event.amount = amount.value;
    });
    second.append(field("实际变化量（可选）", amount));
    card.append(second);

    const hpAfter = document.createElement("input");
    hpAfter.type = "number";
    hpAfter.min = "0";
    hpAfter.max = event.targetSide === "theirs" ? "100" : "99999";
    hpAfter.step = "1";
    hpAfter.value = event.hpAfter;
    hpAfter.placeholder =
      event.targetSide === "theirs" ? "变化后剩（%）" : "变化后具体生命";
    hpAfter.addEventListener("input", () => {
      event.hpAfter = hpAfter.value;
    });
    card.append(
      field(
        event.targetSide === "theirs"
          ? "变化后对方剩（%）"
          : "变化后我方生命",
        hpAfter
      )
    );

    const remove = element("button", "btn btn-small btn-danger", "删除这条");
    remove.type = "button";
    remove.addEventListener("click", () => {
      battleDraftHpEvents.splice(index, 1);
      renderBattleHpEvents();
    });
    card.append(remove);
    box.append(card);
  });
}

function buildMarkEditor(side, kind, labelText) {
  const key = `${side}_marks`;
  const wrapper = element("div", "battle-mark-editor");
  wrapper.append(element("span", "field-title", labelText));
  const select = document.createElement("select");
  const names = kind === "positive" ? BATTLE_POSITIVE_MARKS : BATTLE_NEGATIVE_MARKS;
  populateSelect(
    select,
    [["", "无"]].concat(names.map((name) => [name, name])),
    (battleDraft[key] && battleDraft[key][kind] && battleDraft[key][kind].name) || ""
  );
  const layers = document.createElement("input");
  layers.type = "number";
  layers.min = "0";
  layers.max = "99";
  layers.step = "1";
  layers.placeholder = "层数";
  layers.value =
    battleDraft[key] && battleDraft[key][kind]
      ? String(battleDraft[key][kind].layers)
      : "0";
  layers.disabled = !select.value;
  select.addEventListener("change", () => {
    if (!select.value) {
      battleDraft[key][kind] = null;
      layers.disabled = true;
      layers.value = "0";
    } else {
      const old = battleDraft[key][kind];
      const value = Number(layers.value) || 1;
      battleDraft[key][kind] = {
        name: select.value,
        layers: old && old.name === select.value ? old.layers : value,
      };
      layers.disabled = false;
      layers.value = String(battleDraft[key][kind].layers);
    }
  });
  layers.addEventListener("input", () => {
    if (!select.value) {
      return;
    }
    const value = Math.max(0, Math.min(99, Number(layers.value) || 0));
    if (value === 0) {
      battleDraft[key][kind] = null;
      select.value = "";
      layers.disabled = true;
      return;
    }
    battleDraft[key][kind] = { name: select.value, layers: value };
  });
  wrapper.append(select, layers);
  return wrapper;
}

function buildBuffGrid(side, slot, type) {
  const entry = battleEntry(battleDraft, side, slot);
  const grid = element("div", "battle-buff-grid");
  for (const key of BUFF_KEYS) {
    const input = document.createElement("input");
    input.type = "number";
    input.min = "-99";
    input.max = "99";
    input.step = "1";
    input.value = String(entry.buffs[type][key] || 0);
    input.addEventListener("input", () => {
      const value = Math.max(-99, Math.min(99, Number(input.value) || 0));
      entry.buffs[type][key] = value;
    });
    grid.append(field(STAT_NAMES[key], input));
  }
  return grid;
}

function buildPetSnapshotRow(side, slot) {
  const entry = battleEntry(battleDraft, side, slot);
  const card = element("div", "battle-snapshot-pet");
  const head = element("div", "battle-snapshot-pet-head");
  const team = side === "our" ? state.ourTeam : state.oppTeam;
  const pet = team[slot] ? meta.pets[team[slot].petIndex] : null;
  if (pet) {
    head.append(thumb(pet.avatar, pet.name, "battle-snapshot-avatar"));
  }
  head.append(
    element(
      "strong",
      "",
      `${battleSideText(side)} 第 ${slot + 1} 只 · ${battlePetName(side, slot)}`
    )
  );
  card.append(head);

  const row = element("div", "record-field-pair");
  const hp = document.createElement("input");
  hp.type = "number";
  hp.min = "0";
  hp.step = "1";
  if (side === "our") {
    hp.max = String(entry.max_hp);
    hp.value = String(entry.hp);
  } else {
    hp.max = "100";
    hp.value = String(entry.hp_percent);
  }
  hp.addEventListener("input", () => {
    if (side === "our") {
      entry.hp = Math.max(0, Math.min(entry.max_hp, Number(hp.value) || 0));
    } else {
      entry.hp_percent = Math.max(0, Math.min(100, Number(hp.value) || 0));
    }
  });
  row.append(field(side === "our" ? "生命" : "生命百分比", hp));

  const energy = document.createElement("input");
  energy.type = "number";
  energy.min = "0";
  energy.max = String(entry.max_energy);
  energy.step = "1";
  energy.value = String(entry.energy);
  energy.addEventListener("input", () => {
    entry.energy = Math.max(
      0,
      Math.min(entry.max_energy, Number(energy.value) || 0)
    );
  });
  row.append(field("能量", energy));
  card.append(row);

  const details = document.createElement("details");
  details.append(element("summary", "configure-summary", "增减益 / 状态"));
  const body = element("div", "battle-snapshot-advanced");
  body.append(element("div", "field-title", "非永久增减益（离场消失）"));
  body.append(buildBuffGrid(side, slot, "temporary"));
  body.append(element("div", "field-title", "永久增减益（离场保留）"));
  body.append(buildBuffGrid(side, slot, "permanent"));

  const statusKey = `${side}-${slot}`;
  const statusInput = document.createElement("input");
  statusInput.type = "text";
  statusInput.placeholder = "中毒:2,灼烧:1";
  statusInput.value = battleStatusText[statusKey] || "";
  statusInput.addEventListener("input", () => {
    battleStatusText[statusKey] = statusInput.value;
    try {
      entry.statuses = parseBattleStatusText(
        statusInput.value,
        battlePetName(side, slot)
      );
      statusInput.classList.remove("invalid");
    } catch (error) {
      statusInput.classList.add("invalid");
    }
  });
  body.append(field("状态（逗号分隔）", statusInput));
  details.append(body);
  card.append(details);
  return card;
}

function renderBattleSnapshotEditor() {
  const box = $("battle-snapshot-editor");
  box.replaceChildren();
  if (!battleDraft) {
    return;
  }

  const afterRow = element("div", "battle-snapshot-top");
  const ourActive = document.createElement("select");
  populateSelect(
    ourActive,
    slotOptions("our"),
    battleDraft.active_our
  );
  ourActive.addEventListener("change", () => {
    battleDraft.active_our = Number(ourActive.value);
  });
  afterRow.append(field("回合结束我方场上", ourActive));

  const oppActive = document.createElement("select");
  populateSelect(
    oppActive,
    slotOptions("opp"),
    battleDraft.active_opp
  );
  oppActive.addEventListener("change", () => {
    battleDraft.active_opp = Number(oppActive.value);
  });
  afterRow.append(field("回合结束对方场上", oppActive));
  box.append(afterRow);

  const replacementRow = element("div", "battle-snapshot-top");
  const ourReplacement = document.createElement("select");
  populateSelect(
    ourReplacement,
    slotOptions("our", true),
    battleDraftReplacements.our ?? ""
  );
  ourReplacement.addEventListener("change", () => {
    battleDraftReplacements.our =
      ourReplacement.value === "" ? null : Number(ourReplacement.value);
  });
  replacementRow.append(field("我方击倒补位", ourReplacement));

  const oppReplacement = document.createElement("select");
  populateSelect(
    oppReplacement,
    slotOptions("opp", true),
    battleDraftReplacements.opp ?? ""
  );
  oppReplacement.addEventListener("change", () => {
    battleDraftReplacements.opp =
      oppReplacement.value === "" ? null : Number(oppReplacement.value);
  });
  replacementRow.append(field("对方击倒补位", oppReplacement));
  box.append(replacementRow);

  const fieldRow = element("div", "battle-snapshot-top");
  const weatherSelect = document.createElement("select");
  populateSelect(
    weatherSelect,
    [["", "无天气"]].concat(
      BATTLE_WEATHER_OPTIONS.map((name) => [name, name])
    ),
    battleDraft.weather ? battleDraft.weather.type : ""
  );
  const weatherRemaining = document.createElement("input");
  weatherRemaining.type = "number";
  weatherRemaining.min = "0";
  weatherRemaining.max = "99";
  weatherRemaining.step = "1";
  weatherRemaining.value = battleDraft.weather
    ? String(battleDraft.weather.remaining)
    : "0";
  weatherRemaining.disabled = !weatherSelect.value;
  weatherSelect.addEventListener("change", () => {
    if (!weatherSelect.value) {
      battleDraft.weather = null;
      weatherRemaining.disabled = true;
      weatherRemaining.value = "0";
    } else {
      battleDraft.weather = {
        type: weatherSelect.value,
        remaining: Number(weatherRemaining.value) || 0,
      };
      weatherRemaining.disabled = false;
    }
  });
  weatherRemaining.addEventListener("input", () => {
    if (battleDraft.weather) {
      battleDraft.weather.remaining = Math.max(
        0,
        Number(weatherRemaining.value) || 0
      );
    }
  });
  fieldRow.append(field("天气", weatherSelect));
  fieldRow.append(field("天气剩余回合", weatherRemaining));

  const ourMagic = document.createElement("input");
  ourMagic.type = "number";
  ourMagic.min = "0";
  ourMagic.max = "4";
  ourMagic.step = "1";
  ourMagic.value = String(battleDraft.magic_points.our);
  ourMagic.addEventListener("input", () => {
    battleDraft.magic_points.our = Math.max(
      0,
      Math.min(4, Number(ourMagic.value) || 0)
    );
  });
  fieldRow.append(field("我方魔力值", ourMagic));

  const oppMagic = document.createElement("input");
  oppMagic.type = "number";
  oppMagic.min = "0";
  oppMagic.max = "4";
  oppMagic.step = "1";
  oppMagic.value = String(battleDraft.magic_points.opp);
  oppMagic.addEventListener("input", () => {
    battleDraft.magic_points.opp = Math.max(
      0,
      Math.min(4, Number(oppMagic.value) || 0)
    );
  });
  fieldRow.append(field("对方魔力值", oppMagic));
  box.append(fieldRow);
  box.append(
    element(
      "p",
      "hint",
      "有精灵被击倒时，记得把对应一方的魔力值减 1；后台也会检查击倒数。"
    )
  );

  const marksRow = element("div", "battle-marks-grid");
  marksRow.append(buildMarkEditor("our", "positive", "我方正面印记"));
  marksRow.append(buildMarkEditor("our", "negative", "我方负面印记"));
  marksRow.append(buildMarkEditor("opp", "positive", "对方正面印记"));
  marksRow.append(buildMarkEditor("opp", "negative", "对方负面印记"));
  box.append(marksRow);

  for (const side of ["our", "opp"]) {
    const entries = battleSideEntries(battleDraft, side);
    for (let slot = 0; slot < 6; slot += 1) {
      if (entries[slot]) {
        box.append(buildPetSnapshotRow(side, slot));
      }
    }
  }
}

function renderBattleTurnList() {
  const box = $("battle-turn-list");
  box.replaceChildren();
  const turns = state.battle.turns;
  if (!turns.length) {
    box.append(element("li", "empty-note", "还没有添加回合。"));
    return;
  }
  const inferenceTurns =
    state.battle.inference && Array.isArray(state.battle.inference.turns)
      ? state.battle.inference.turns
      : [];
  turns.forEach((turn, index) => {
    const item = element("li", "record-card");
    const head = element("div", "record-head");
    head.append(element("strong", "", `第 ${index + 1} 回合`));
    const info = inferenceTurns[index];
    head.append(
      element(
        "span",
        "record-context",
        `我方 ${battleActionText(turn.our_action)} · 对方 ${battleActionText(
          turn.opp_action
        )} · 先手 ${turn.who_first === "our" ? "我方" : "对方"}`
      )
    );
    item.append(head);
    if (info) {
      item.append(
        element(
          "div",
          "record-summary",
          `应对 ${info.counter ? "是" : "否"} · 可推断速度 ${
            info.speed_inferable ? "是" : "否"
          }`
        )
      );
      for (const event of info.events || []) {
        const kind = event.type === "damage" ? "伤害" : "生命变化";
        let text = event.matched
          ? `${kind}：匹配成功`
          : `${kind}：未匹配或未参与推断`;
        if (event.matched && event.kept !== null && event.kept !== undefined) {
          text += `，剩 ${numberText(event.kept)} 种`;
        }
        if (event.message) {
          text += `；${event.message}`;
        }
        item.append(element("div", "record-result-text", text));
      }
      for (const warning of info.warnings || []) {
        item.append(element("div", "record-warning", warning));
      }
    } else {
      item.append(element("div", "record-context", "等待对局计算…"));
    }
    const remove = element(
      "button",
      "btn btn-small btn-danger record-delete",
      "从这回合起重填"
    );
    remove.type = "button";
    remove.addEventListener("click", () => {
      deleteBattleFrom(index);
    });
    item.append(remove);
    box.append(item);
  });
}

function renderBattleWarnings() {
  const box = $("battle-warnings");
  if (!box) {
    return;
  }
  const warnings =
    state.battle.inference && Array.isArray(state.battle.inference.warnings)
      ? state.battle.inference.warnings
      : [];
  box.replaceChildren();
  if (!warnings.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.append(element("strong", "warning-title", "对局提示"));
  const list = element("ul", "warning-list");
  for (const text of warnings) {
    list.append(element("li", "", text));
  }
  box.append(list);
}

function syncBattleActiveAfter() {
  if (!battleDraft) {
    return false;
  }
  let changed = false;
  const ourType = $("battle-our-action-type").value;
  const oppType = $("battle-opp-action-type").value;
  if (ourType === "switch" && $("battle-our-switch").value !== "") {
    const next = Number($("battle-our-switch").value);
    if (battleDraft.active_our !== next) {
      battleDraft.active_our = next;
      changed = true;
    }
  }
  if (oppType === "switch" && $("battle-opp-switch").value !== "") {
    const next = Number($("battle-opp-switch").value);
    if (battleDraft.active_opp !== next) {
      battleDraft.active_opp = next;
      changed = true;
    }
  }
  return changed;
}

function battleActionFromSelect(side, pre) {
  const prefix = side === "our" ? "battle-our" : "battle-opp";
  const type = $(`${prefix}-action-type`).value;
  const slot = side === "our" ? pre.our : pre.opp;
  if (type === "none") {
    return null;
  }
  if (type === "charge") {
    return { type: "charge", slot };
  }
  if (type === "switch") {
    const raw = $(`${prefix}-switch`).value;
    if (raw === "") {
      throw new Error(`${battleSideText(side)}选择了切换，但没有选目标。`);
    }
    return { type: "switch", slot, to_slot: Number(raw) };
  }
  const skill = $(`${prefix}-skill`).value;
  if (!skill) {
    throw new Error(`${battleSideText(side)}选择了使用技能，但没有选技能。`);
  }
  return { type: "skill", slot, skill };
}

function battleIntendedSkill(side) {
  const prefix = side === "our" ? "battle-our" : "battle-opp";
  if ($(`${prefix}-action-type`).value !== "none") {
    return null;
  }
  const value = $(`${prefix}-intended-skill`).value;
  return value || null;
}

function requiredInt(value, label, min, max, allowBlank = false) {
  if ((value === "" || value === null || value === undefined) && allowBlank) {
    return null;
  }
  const number = Number(value);
  if (!Number.isInteger(number) || number < min || (max !== null && number > max)) {
    throw new Error(`${label}必须是 ${min}${max !== null ? `～${max}` : " 以上"} 的整数。`);
  }
  return number;
}

function buildBattleEventsFromDraft(pre, whoFirst) {
  const events = [];
  const firstSide = whoFirst === "opp" ? "theirs" : "our";
  const damageDrafts = [...battleDraftDamageEvents].sort((a, b) => {
    const aFirst = a.sourceSide === firstSide ? 0 : 1;
    const bFirst = b.sourceSide === firstSide ? 0 : 1;
    return aFirst - bFirst;
  });
  for (const event of damageDrafts) {
    if (event.totalDamage === "" || event.totalDamage === null) {
      continue;
    }
    const total = requiredInt(event.totalDamage, "总伤害", 0, null);
    const hits = requiredInt(event.hits, "连击数", 1, 99);
    const ignore = Boolean(event.ignore);
    const hpAfter = requiredInt(
      event.hpAfter,
      event.targetSide === "theirs" ? "对方行动后剩余百分比" : "我方行动后生命",
      0,
      event.targetSide === "theirs" ? 100 : null,
      ignore
    );
    const after =
      hpAfter === null
        ? null
        : event.targetSide === "theirs"
          ? { side: "theirs", slot: event.targetSlot, hp_percent: hpAfter }
          : { side: "our", slot: event.targetSlot, hp: hpAfter };
    if (!after) {
      throw new Error(`伤害事件“${event.skill}”必须填写打完后的生命。`);
    }
    events.push({
      type: "damage",
      source_side: event.sourceSide,
      source_slot: event.sourceSlot,
      target_side: event.targetSide,
      target_slot: event.targetSlot,
      skill: event.skill,
      total_damage: total,
      hits,
      hp_after: after,
      overkill: Boolean(event.overkill),
      ignore_for_inference: ignore,
      context: battleContextFromSnapshot(
        pre.snapshot,
        event.sourceSide,
        event.sourceSlot,
        event.targetSide,
        event.targetSlot
      ),
    });
  }
  for (const event of battleDraftHpEvents) {
    const hpAfter = requiredInt(
      event.hpAfter,
      event.targetSide === "theirs" ? "变化后对方剩余百分比" : "变化后我方生命",
      0,
      event.targetSide === "theirs" ? 100 : null
    );
    const amount = requiredInt(
      event.amount,
      "实际变化量",
      -99999,
      99999,
      true
    );
    const after =
      event.targetSide === "theirs"
        ? { side: "theirs", slot: event.targetSlot, hp_percent: hpAfter }
        : { side: "our", slot: event.targetSlot, hp: hpAfter };
    const item = {
      type: "hp_change",
      target_side: event.targetSide,
      target_slot: event.targetSlot,
      reason: event.reason,
      hp_after: after,
    };
    if (amount !== null) {
      item.amount = amount;
    }
    events.push(item);
  }
  return events;
}

function buildBattleTurnFromForm() {
  const pre = battlePreSlots();
  if (!pre || !battleDraft) {
    throw new Error("还没有对局初始状态。");
  }
  const ourAction = battleActionFromSelect("our", pre);
  const oppAction = battleActionFromSelect("opp", pre);
  if (!ourAction && !oppAction) {
    throw new Error("双方行动不能同时为空。");
  }
  for (const side of ["our", "opp"]) {
    const entries = battleSideEntries(battleDraft, side);
    for (let slot = 0; slot < 6; slot += 1) {
      if (!entries[slot]) {
        continue;
      }
      const key = `${side}-${slot}`;
      entries[slot].statuses = parseBattleStatusText(
        battleStatusText[key] || "",
        battlePetName(side, slot)
      );
    }
  }
  const snapshot = clone(battleDraft);
  const whoFirst =
    !ourAction ? "opp" : !oppAction ? "our" : $("battle-who-first").value;
  const turn = {
    turn: state.battle.turns.length + 1,
    our_action: ourAction,
    opp_action: oppAction,
    who_first: whoFirst,
    counter: $("battle-counter").checked,
    events: buildBattleEventsFromDraft(pre, whoFirst),
    replacements: clone(battleDraftReplacements),
    snapshot_after: snapshot,
  };
  const ourIntended = battleIntendedSkill("our");
  const oppIntended = battleIntendedSkill("opp");
  if (ourIntended) {
    turn.our_intended_skill = ourIntended;
  }
  if (oppIntended) {
    turn.opp_intended_skill = oppIntended;
  }
  return turn;
}

async function requestBattleAnalysis() {
  if (!state.battle.initial) {
    throw new Error("还没有对局初始状态。");
  }
  const current = ++battleAnalysisId;
  const response = await fetch("/api/battle", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      version: 1,
      initial: state.battle.initial,
      turns: state.battle.turns,
      result: state.battle.result,
    }),
  });
  let data = null;
  try {
    data = await response.json();
  } catch (error) {
    data = null;
  }
  if (current !== battleAnalysisId) {
    return null;
  }
  if (!response.ok) {
    throw new Error(
      data && data.error ? data.error : `服务返回了 ${response.status}`
    );
  }
  if (!data) {
    throw new Error("服务返回的内容不是有效数据。");
  }
  return data;
}

async function analyzeBattleAndRender() {
  setBattleStatus("对局计算中…");
  try {
    const data = await requestBattleAnalysis();
    if (!data) {
      return null;
    }
    state.battle.inference = data;
    state.battle.result = data.result;
    resetBattleDraft(data.snapshot);
    saveState();
    updateSlotSummaries(data);
    renderBattleCard();
    setBattleStatus("");
    return data;
  } catch (error) {
    setBattleStatus(`对局计算失败：${friendlyError(error)}`, true);
    throw error;
  }
}

async function addBattleTurn() {
  const wasStarted = battleStarted();
  try {
    if (!state.battle.initial) {
      state.battle.initial = initialFromTeamConfig();
    }
    const turn = buildBattleTurnFromForm();
    state.battle.turns.push(turn);
    saveState();
    const data = await requestBattleAnalysis();
    if (!data) {
      return;
    }
    const last = state.battle.turns.length - 1;
    if (data.turns && data.turns[last]) {
      state.battle.turns[last].counter = data.turns[last].counter;
      state.battle.turns[last].speed_inferable =
        data.turns[last].speed_inferable;
    }
    state.battle.inference = data;
    state.battle.result = data.result;
    resetBattleDraft(data.snapshot);
    saveState();
    renderTeams();
    updateSlotSummaries(data);
    renderBattleCard();
    setBattleStatus("");
    scheduleCalculate();
  } catch (error) {
    state.battle.turns.pop();
    if (!wasStarted && state.battle.turns.length === 0) {
      state.battle.initial = null;
      battleDraft = null;
      battlePreBase = null;
    }
    saveState();
    renderTeams();
    renderBattleCard();
    setBattleStatus(`添加回合失败：${friendlyError(error)}`, true);
  }
}

async function deleteBattleFrom(index) {
  if (!window.confirm(`确定删除第 ${index + 1} 回合及之后的所有回合吗？`)) {
    return;
  }
  const backup = clone(state);
  state.battle.turns = state.battle.turns.slice(0, index);
  state.battle.result = null;
  state.battle.inference = null;
  battleDraft = null;
  saveState();
  if (state.battle.turns.length) {
    try {
      await analyzeBattleAndRender();
      renderTeams();
      scheduleCalculate();
    } catch (error) {
      state = backup;
      saveState();
      renderTeams();
      renderBattleCard();
    }
  } else {
    renderTeams();
    renderBattleCard();
    scheduleCalculate();
  }
}

function clearBattle() {
  if (!window.confirm("确定清空整场对局记录吗？队伍配置会保留，但旧记录无法恢复。")) {
    return;
  }
  state.battle = freshBattle();
  battleDraft = null;
  battlePreBase = null;
  battleStatusText = {};
  saveState();
  renderTeams();
  renderBattleCard();
  scheduleCalculate();
}

function downloadJson(filename, payload) {
  const blob = new Blob([JSON.stringify(payload, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = filename;
  document.body.append(link);
  link.click();
  link.remove();
  URL.revokeObjectURL(url);
}

function exportBattle() {
  if (!state.battle.initial) {
    setBattleStatus("还没有对局可以导出。", true);
    return;
  }
  const stamp = new Date().toISOString().replace(/[:.]/g, "-");
  downloadJson(`roco-battle-${stamp}.json`, {
    format: "roco-battle",
    version: 1,
    raw: {
      version: 1,
      initial: state.battle.initial,
      turns: state.battle.turns,
      result: state.battle.result,
    },
    our_config: state.battle.initial.our_team,
    opponent_inference:
      state.battle.inference && state.battle.inference.opponent_team
        ? state.battle.inference.opponent_team
        : [],
  });
  setBattleStatus("对局文件已导出。");
}

function compareBattleInference(saved, computed) {
  const notes = [];
  if (!Array.isArray(saved) || !Array.isArray(computed)) {
    return notes;
  }
  for (let slot = 0; slot < 6; slot += 1) {
    const before = saved[slot];
    const after = computed[slot];
    if (!before || !after || !before.configured || !after.configured) {
      continue;
    }
    if (
      before.builds &&
      after.builds &&
      before.builds.remaining !== after.builds.remaining
    ) {
      notes.push(
        `对方第 ${slot + 1} 只：文件里剩 ${before.builds.remaining} 种，重新计算剩 ${after.builds.remaining} 种`
      );
    }
    if (before.natures_remaining !== after.natures_remaining) {
      notes.push(
        `对方第 ${slot + 1} 只：文件里 ${before.natures_remaining} 种性格，重新计算 ${after.natures_remaining} 种`
      );
    }
  }
  return notes;
}

async function importBattle(file) {
  const backup = clone(state);
  try {
    const text = await file.text();
    const payload = JSON.parse(text);
    const raw = payload.raw && typeof payload.raw === "object" ? payload.raw : payload;
    if (!raw || typeof raw !== "object" || !raw.initial) {
      throw new Error("这个文件里没有对局初始状态。");
    }
    if (raw.version !== undefined && raw.version !== 1) {
      throw new Error(`暂不支持对局文件版本 ${raw.version}。`);
    }
    const team = teamFromBattleInitial(raw.initial);
    state.ourTeam = team.ourTeam;
    state.oppTeam = team.oppTeam;
    state.activeOur = team.activeOur;
    state.activeOpp = team.activeOpp;
    state.battle = {
      initial: raw.initial,
      turns: Array.isArray(raw.turns) ? raw.turns : [],
      result: raw.result || null,
      inference: null,
    };
    battleDraft = null;
    battlePreBase = null;
    battleStatusText = {};
    saveState();
    renderTeams();
    renderBattleCard();
    const data = await requestBattleAnalysis();
    if (!data) {
      return;
    }
    state.battle.inference = data;
    state.battle.result = data.result;
    resetBattleDraft(data.snapshot);
    saveState();
    updateSlotSummaries(data);
    renderBattleCard();
    scheduleCalculate();
    const notes = compareBattleInference(
      payload.opponent_inference,
      data.opponent_team
    );
    if (notes.length) {
      setBattleStatus(
        `导入完成，但保存的推断结果和重新计算不一致：${notes.join("；")}`,
        true
      );
    } else {
      setBattleStatus("对局文件已导入，并重新计算完成。");
    }
  } catch (error) {
    state = backup;
    saveState();
    renderTeams();
    renderBattleCard();
    scheduleCalculate();
    setBattleStatus(`导入失败：${friendlyError(error)}`, true);
  }
}

function ocrPetSelectOptions() {
  return [["", "— 未指定 —"]].concat(
    petOptions.map((item) => [String(item.index), item.label])
  );
}

function renderOcrStatus(status) {
  const box = $("ocr-status");
  box.replaceChildren();
  box.classList.toggle("ready", Boolean(status && status.ready));
  box.append(element("div", "", status ? status.message : "无法读取 OCR 状态。"));
  if (status && status.layouts) {
    box.append(
      element(
        "div",
        "hint",
        `布局：${status.layouts.map((item) => item.label).join("；")}`
      )
    );
  }
  if (!status || !status.ready) {
    const retry = element("button", "btn btn-small btn-secondary", "重新检查");
    retry.type = "button";
    retry.addEventListener("click", () => loadOcrStatus(true));
    box.append(retry);
  }
  if ($("ocr-run")) {
    $("ocr-run").disabled = !status || !status.ready;
  }
  if (status && status.layouts) {
    const select = $("ocr-layout");
    const current = select.value;
    populateSelect(
      select,
      status.layouts.map((item) => [item.id, item.label]),
      status.layouts.some((item) => item.id === current)
        ? current
        : status.layouts[0].id
    );
  }
}

async function loadOcrStatus(force = false) {
  const box = $("ocr-status");
  if (box) {
    box.textContent = "正在检查 OCR 环境…";
  }
  try {
    const response = await fetch(`/api/ocr/status${force ? "?force=1" : ""}`);
    const data = await response.json();
    if (!response.ok) {
      throw new Error(data.error || `服务返回了 ${response.status}`);
    }
    ocrStatus = data;
    renderOcrStatus(data);
  } catch (error) {
    renderOcrStatus({
      ready: false,
      message: `OCR 状态读取失败：${friendlyError(error)}`,
      layouts: [],
    });
  }
}

function ocrIdentityIndex(info) {
  if (!info || !info.name) {
    return -1;
  }
  return findPetIndexByRef(info.name, info.form);
}

function ocrSkillNames(info, petIndex) {
  const recognized = (info.skills || [])
    .filter((skill) => skill.name && ["exact", "corrected"].includes(skill.name_status))
    .map((skill) => skill.name);
  const kept = filterSkills(petIndex, recognized);
  const dropped = recognized.filter((name) => !kept.includes(name));
  return { recognized, kept, dropped };
}

function applyOcrPet(side, slot, info) {
  if (battleStarted()) {
    setBattleStatus("对局已开始，队伍已锁定；请先清空对局再应用 OCR 队伍。", true);
    return;
  }
  const petIndex = ocrIdentityIndex(info);
  if (petIndex < 0) {
    setBattleStatus(
      "OCR 没有确认这只精灵的数据库身份（显示名可能只是玩家昵称），不能自动填入。",
      true
    );
    return;
  }
  const { kept, dropped } = ocrSkillNames(info, petIndex);
  if (side === "our") {
    const old = state.ourTeam[slot];
    const slotData =
      old && old.petIndex === petIndex ? old : freshOurSlot(petIndex);
    slotData.skills = kept.slice(0, 4);
    state.ourTeam[slot] = slotData;
    state.activeOur = slot;
  } else {
    const slotData = freshOppSlot(petIndex);
    slotData.skills = kept;
    state.oppTeam[slot] = slotData;
    state.activeOpp = slot;
  }
  saveState();
  renderTeams();
  refreshActiveStyles();
  renderBattleCard();
  scheduleCalculate();
  const droppedText = dropped.length
    ? `；未写入不在可学表的技能：${dropped.join("、")}`
    : "";
  setBattleStatus(
    `已把 ${battleSideText(side)} ${info.display_name || info.name} 填入第 ${slot + 1} 只${droppedText}。`,
    dropped.length > 0
  );
}

function renderOcrSide(side, info) {
  const card = element("div", `ocr-side ${side}`);
  const title = element(
    "div",
    "ocr-side-title",
    side === "our" ? "我方" : "对方"
  );
  card.append(title);
  card.append(
    element(
      "div",
      "ocr-meta",
      info.name
        ? `确认身份：${info.name}${info.form ? `（${info.form}）` : ""} · 编号 ${info.number || "?"} · 来源 ${
            {
              provided: "手动指定",
              avatar: "头像",
              avatar_and_name: "头像+显示名",
            }[info.identity_source] || info.identity_source || "未知"
          }`
        : "确认身份：未确认；显示名不能直接当成精灵原名"
    )
  );
  const detectedTypes = info.types;
  if (detectedTypes) {
    if (detectedTypes.status === "unavailable") {
      card.append(
        element(
          "div",
          "ocr-meta",
          "属性：本机还没有属性图标，运行 python download_assets.py --only types 后可用"
        )
      );
    } else if (detectedTypes.primary) {
      const names = [detectedTypes.primary, detectedTypes.secondary].filter(Boolean);
      card.append(
        element("div", "ocr-meta", `属性：${names.join(" / ")}（头像左下角图标）`)
      );
    } else {
      card.append(element("div", "ocr-meta", "属性：未识别"));
    }
  }
  const readouts = [];
  if (info.hp !== undefined && info.hp !== null) {
    readouts.push(`生命 ${info.hp}/${info.max_hp}`);
  }
  if (info.hp_percent !== undefined && info.hp_percent !== null) {
    readouts.push(`生命 ${info.hp_percent}%`);
  }
  if (info.energy !== undefined) {
    readouts.push(`能量 ${info.energy === null ? "未读出" : info.energy}`);
  }
  card.append(element("div", "ocr-meta", readouts.join(" · ") || "生命/能量：未读出"));
  if (info.avatar_match) {
    card.append(
      element(
        "div",
        "ocr-meta",
        `头像状态：${info.avatar_match.status} · ${info.avatar_match.requires_review ? "需要确认" : "可参考"}`
      )
    );
  }

  const skillList = element("div", "ocr-skills");
  for (const skill of info.skills || []) {
    const category = skill.category || "未识别";
    const power = skill.power === null || skill.power === undefined
      ? "无/未读出"
      : skill.power;
    let cost;
    if (skill.energy_cost !== null && skill.energy_cost !== undefined) {
      cost = String(skill.energy_cost);
      if (skill.energy_cost_status === "database_supported") {
        cost += "（数据库辅助）";
      }
    } else if (
      skill.energy_cost_status === "database_estimate" &&
      skill.energy_cost_estimate !== null &&
      skill.energy_cost_estimate !== undefined
    ) {
      cost = `?（数据库推定 ${skill.energy_cost_estimate}）`;
    } else {
      cost = "?";
    }
    const cooldown =
      skill.cooldown !== null && skill.cooldown !== undefined
        ? ` · 冷却 ${skill.cooldown} 回合`
        : "";
    skillList.append(
      element(
        "div",
        "ocr-skill",
        `${skill.slot}. ${skill.name || "未识别"} · ${category} · 威力 ${power} · 能耗 ${cost}${cooldown}`
      )
    );
  }
  card.append(skillList);

  const apply = element("div", "ocr-apply");
  const slotSelect = document.createElement("select");
  for (let index = 0; index < 6; index += 1) {
    const option = document.createElement("option");
    option.value = String(index);
    option.textContent = `第 ${index + 1} 只`;
    slotSelect.append(option);
  }
  const button = element(
    "button",
    "btn btn-small",
    side === "our" ? "填入我方队伍" : "填入对方队伍"
  );
  button.type = "button";
  button.disabled = battleStarted() || !info.name;
  button.addEventListener("click", () => {
    applyOcrPet(side, Number(slotSelect.value), info);
  });
  apply.append(slotSelect, button);
  card.append(apply);
  if (!info.name) {
    card.append(
      element(
        "p",
        "hint",
        "没有确认身份时，可以在上方 OCR 面板的“我方/对方精灵”里手动指定后重新识别，或直接在队伍卡片里选择。"
      )
    );
  }
  return card;
}

function renderOcrResult(data) {
  const box = $("ocr-result");
  box.replaceChildren();
  if (!data) {
    return;
  }
  const grid = element("div", "ocr-result-grid");
  grid.append(
    renderOcrSide("our", {
      ...(data.our || {}),
      skills: data.skills || [],
    })
  );
  grid.append(renderOcrSide("opponent", data.opponent || {}));
  box.append(grid);
  box.append(
    element(
      "p",
      "hint",
      "OCR 只填精灵和技能；能量/生命读数用于核对。对局开始后请在回合记录里填写实际状态。"
    )
  );
  if (data.debug) {
    const details = document.createElement("details");
    details.append(element("summary", "configure-summary", "OCR 原文与调试信息"));
    details.append(
      element("pre", "ocr-debug", JSON.stringify(data.debug, null, 2))
    );
    box.append(details);
  }
}

async function runOcrFrame() {
  const file = $("ocr-file").files[0];
  if (!file) {
    setOcrError("请先选择一张操作帧截图。", true);
    return;
  }
  if (!ocrStatus || !ocrStatus.ready) {
    setOcrError("OCR 环境还没有就绪，请先按提示安装/下载模型。", true);
    return;
  }
  const params = new URLSearchParams();
  params.set("layout", $("ocr-layout").value);
  const ourIndex = $("ocr-our-pet").value;
  const oppIndex = $("ocr-opponent-pet").value;
  if (ourIndex !== "") {
    const pet = meta.pets[Number(ourIndex)];
    params.set("our_pet", pet.name);
    if (pet.form) {
      params.set("our_form", pet.form);
    }
  }
  if (oppIndex !== "") {
    const pet = meta.pets[Number(oppIndex)];
    params.set("opponent_pet", pet.name);
    if (pet.form) {
      params.set("opponent_form", pet.form);
    }
  }
  if ($("ocr-no-avatar").checked) {
    params.set("no_avatar", "1");
  }
  if ($("ocr-debug").checked) {
    params.set("debug", "1");
  }
  const button = $("ocr-run");
  button.disabled = true;
  setOcrError("OCR 识别中；第一次加载模型会更慢……");
  try {
    const response = await fetch(`/api/ocr/frame?${params.toString()}`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream" },
      body: file,
    });
    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    if (!response.ok) {
      throw new Error(data && data.error ? data.error : `服务返回了 ${response.status}`);
    }
    ocrResult = data;
    setOcrError("");
    renderOcrResult(data);
  } catch (error) {
    setOcrError(`OCR 失败：${friendlyError(error)}`, true);
  } finally {
    button.disabled = !ocrStatus || !ocrStatus.ready;
  }
}

function setOcrError(text, isError = false) {
  const node = $("ocr-error");
  node.textContent = text || "";
  node.classList.toggle("error", Boolean(isError));
  node.hidden = !text;
}

function initOcrPanel() {
  populateSelect($("ocr-our-pet"), ocrPetSelectOptions(), "");
  populateSelect($("ocr-opponent-pet"), ocrPetSelectOptions(), "");
  $("ocr-run").addEventListener("click", runOcrFrame);
  $("ocr-file").addEventListener("change", () => {
    setOcrError("");
    $("ocr-result").replaceChildren();
  });
  loadOcrStatus();
}

function initBattleForm() {
  const onChange = () => {
    renderBattleActionSelectors(battlePreSlots());
    const activeChanged = syncBattleActiveAfter();
    syncBattleDamageEvents();
    renderBattleDamageEvents();
    if (activeChanged) {
      renderBattleSnapshotEditor();
    }
  };
  $("battle-our-action-type").addEventListener("change", onChange);
  $("battle-opp-action-type").addEventListener("change", onChange);
  $("battle-our-skill").addEventListener("change", onChange);
  $("battle-opp-skill").addEventListener("change", onChange);
  $("battle-our-switch").addEventListener("change", onChange);
  $("battle-opp-switch").addEventListener("change", onChange);
  $("battle-show-all-opp-skills").addEventListener("change", () => {
    renderBattleActionSelectors(battlePreSlots());
    syncBattleDamageEvents();
    renderBattleDamageEvents();
  });
  $("battle-add-hp-event").addEventListener("click", () => {
    const oppSlots = configuredSlots("opp");
    battleDraftHpEvents.push({
      targetSide: oppSlots.length ? "theirs" : "our",
      targetSlot: oppSlots.length
        ? oppSlots[0]
        : configuredSlots("our")[0] ?? 0,
      reason: "other",
      amount: "",
      hpAfter: "",
    });
    renderBattleHpEvents();
  });
  $("battle-add-turn").addEventListener("click", () => {
    addBattleTurn();
  });
  $("battle-export").addEventListener("click", exportBattle);
  $("battle-import-button").addEventListener("click", () => {
    $("battle-import-file").click();
  });
  $("battle-import-file").addEventListener("change", async () => {
    const file = $("battle-import-file").files[0];
    $("battle-import-file").value = "";
    if (file) {
      await importBattle(file);
    }
  });
  $("battle-clear").addEventListener("click", clearBattle);
  renderBattleCard();
}

function renderWarnings(warnings) {
  const box = $("calc-warnings") || $("warnings");
  if (!box) {
    return;
  }
  box.replaceChildren();
  if (!warnings || !warnings.length) {
    box.hidden = true;
    return;
  }
  box.hidden = false;
  box.append(element("strong", "warning-title", "提示"));
  const list = element("ul", "warning-list");
  for (const text of warnings) {
    list.append(element("li", "", text));
  }
  box.append(list);
}

function renderResponse(data) {
  latestTeamResult = data;
  const summaryData =
    battleStarted() && state.battle.inference
      ? state.battle.inference
      : data;
  updateSlotSummaries(summaryData);
  renderActive(data.active, data);
  renderWarnings(data.warnings || []);
  renderBattleCard();
}

function validateState() {
  for (const side of ["our", "opp"]) {
    const label = side === "our" ? "我方" : "对方";
    for (let index = 0; index < 6; index += 1) {
      const slot = getSlot(side, index);
      if (!slot) {
        continue;
      }
      if (side === "our") {
        if (slot.skills.length > 4) {
          return `${label}第 ${index + 1} 只精灵最多只能有 4 个技能。`;
        }
        if (countNonzeroTalents(slot) > 3) {
          return `${label}第 ${index + 1} 只精灵的天分最多只能有三项有加成。`;
        }
        if (
          slot.currentHp !== null &&
          (!Number.isInteger(slot.currentHp) || slot.currentHp < 0)
        ) {
          return `${label}第 ${index + 1} 只精灵的记录起点生命必须是 0 或更大的整数。`;
        }
      }
    }
  }

  for (let index = 0; index < state.records.length; index += 1) {
    const record = state.records[index];
    const label = `第 ${index + 1} 条记录`;
    if (record.type === "damage") {
      const defenderSide = record.attackerSide === "our" ? "opp" : "our";
      if (!getSlot(record.attackerSide, record.attackerSlot)) {
        return `${label}的攻击方位置还没有选择精灵。`;
      }
      if (!getSlot(defenderSide, record.defenderSlot)) {
        return `${label}的防守方位置还没有选择精灵。`;
      }
      if (!skillByName.has(record.skill)) {
        return `${label}的技能“${record.skill}”找不到。`;
      }
    } else if (!getSlot(record.side, record.slot)) {
      return `${label}指向的位置还没有选择精灵。`;
    }
  }
  return null;
}

function slotPayload(side, slot) {
  const pet = meta.pets[slot.petIndex];
  const payload = {
    pet: { name: pet.name, form: pet.form || null },
    buffs: {},
    skills: [...slot.skills],
  };

  if (side === "our") {
    payload.nature = slot.nature || null;
    payload.talents = { ...slot.talents };
    // 没有对局记录时按满血预测；生命和增减益由对局记录提供。
    payload.current_hp = null;
  }
  return payload;
}

function recordPayload(record) {
  if (record.type === "hp_change") {
    const payload = {
      type: "hp_change",
      side: record.side === "our" ? "our" : "theirs",
      slot: record.slot,
    };
    if (record.side === "opp") {
      payload.hp_percent = record.hpPercent;
      if (record.hpDelta !== null) {
        payload.hp_delta = record.hpDelta;
      }
    } else {
      payload.hp = record.hp;
    }
    return payload;
  }

  const defenderSide = record.attackerSide === "our" ? "opp" : "our";
  const payload = {
    type: "damage",
    attacker_side: record.attackerSide === "our" ? "our" : "theirs",
    attacker_slot: record.attackerSlot,
    defender_side: defenderSide === "our" ? "our" : "theirs",
    defender_slot: record.defenderSlot,
    skill: record.skill,
    damage: record.damage,
    weather: record.weather || null,
    attacker_buffs: { ...record.attackerBuffs },
    defender_buffs: { ...record.defenderBuffs },
    starfall_layers: record.starfall,
    hits: record.hits,
  };
  if (record.attackerSide === "our") {
    if (record.hpPercent !== null) {
      payload.hp_percent = record.hpPercent;
    }
  } else if (record.remainingHp !== null) {
    payload.remaining_hp = record.remainingHp;
  }
  return payload;
}

function buildPayload() {
  const payload = {
    level: 60,
    weather: state.weather || null,
    active_our_slot: state.activeOur,
    active_opponent_slot: state.activeOpp,
    our_team: state.ourTeam.map((slot) =>
      slot ? slotPayload("our", slot) : null
    ),
    opponent_team: state.oppTeam.map((slot) =>
      slot ? slotPayload("opp", slot) : null
    ),
    records: state.records.map(recordPayload),
  };
  if (battleStarted()) {
    payload.battle_log = {
      version: 1,
      initial: state.battle.initial,
      turns: state.battle.turns,
      result: state.battle.result,
    };
  }
  return payload;
}

function friendlyError(error) {
  if (error instanceof TypeError) {
    return "无法连接本地服务，请确认运行 server.py 的窗口还开着。";
  }
  return error && error.message ? error.message : String(error);
}

async function calculate() {
  if (!meta) {
    return;
  }

  const problem = validateState();
  if (problem) {
    setStatus(problem, true);
    return;
  }

  const currentRequest = requestId + 1;
  requestId = currentRequest;
  setStatus("计算中…");

  try {
    const response = await fetch("/api/team", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(buildPayload()),
    });
    let data = null;
    try {
      data = await response.json();
    } catch (error) {
      data = null;
    }
    if (currentRequest !== requestId) {
      return;
    }
    if (!response.ok) {
      const message = data && data.error
        ? data.error
        : `服务返回了 ${response.status}`;
      throw new Error(message);
    }
    if (!data) {
      throw new Error("服务返回的内容不是有效数据。");
    }
    renderResponse(data);
    setStatus("");
  } catch (error) {
    if (currentRequest !== requestId) {
      return;
    }
    setStatus(`计算失败：${friendlyError(error)}`, true);
  }
}

function scheduleCalculate() {
  requestId += 1;
  saveState();
  if (calcTimer) {
    clearTimeout(calcTimer);
  }
  calcTimer = setTimeout(() => {
    calcTimer = null;
    calculate();
  }, 180);
}

async function init() {
  setStatus("正在读取精灵资料…");
  try {
    const response = await fetch("/api/meta");
    if (!response.ok) {
      throw new Error(`读取精灵资料失败（${response.status}）`);
    }
    meta = await response.json();
    skillByName = new Map(meta.skills.map((skill) => [skill.name, skill]));
    actionSkillByName = new Map(
      (meta.action_skills || []).map((skill) => [skill.name, skill])
    );
    petOptions = meta.pets
      .map((pet, index) => ({ index, label: petLabel(pet) }))
      .sort((a, b) => a.label.localeCompare(b.label, "zh-CN"));

    restoreState();
    $("weather").value = state.weather;
    $("weather").addEventListener("change", () => {
      state.weather = $("weather").value;
      scheduleCalculate();
    });

    renderTeams();
    refreshActiveStyles();
    initOcrPanel();
    initBattleForm();
    saveState();
    setStatus("");
    await calculate();
    if (battleStarted() && state.battle.turns.length) {
      analyzeBattleAndRender();
    } else {
      renderBattleCard();
    }
  } catch (error) {
    console.error(error);
    setStatus(`页面载入失败：${friendlyError(error)}`, true);
  }
}

init();
