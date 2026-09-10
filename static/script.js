// ============================================================
// Скрипт: копирование, состояние «Готовим…», отложка, избранное
// ============================================================

// Находим нужные элементы на странице
const copyBtn = document.getElementById("copy-btn");
const copyMsg = document.getElementById("copy-msg");
const postBox = document.getElementById("generated-post");
const form = document.getElementById("post-form");
const submitBtn = document.getElementById("submit-btn");
const publishTime = document.getElementById("publish_time");

// Показываем состояние «готовим пост…», пока модель пишет текст.
if (form && submitBtn) {
  form.addEventListener("submit", () => {
    submitBtn.textContent = "Готовим пост для тебя… ✍️";
    submitBtn.disabled = true;
    submitBtn.classList.add("loading");
  });
}

// Общий помощник: скопировать текст (современный способ + запасной)
async function copyText(text) {
  try {
    await navigator.clipboard.writeText(text);
    return true;
  } catch {
    const tempArea = document.createElement("textarea");
    tempArea.value = text;
    document.body.appendChild(tempArea);
    tempArea.select();
    let ok = false;
    try {
      ok = document.execCommand("copy");
    } catch {
      ok = false;
    }
    tempArea.remove();
    return ok;
  }
}

// Кнопка и сообщение существуют только после генерации поста
if (copyBtn && copyMsg && postBox) {
  copyBtn.addEventListener("click", async () => {
    await copyText(postBox.innerText);
    copyMsg.classList.add("visible");
    setTimeout(() => {
      copyMsg.classList.remove("visible");
    }, 2000);
  });
}

// Кнопки «Копировать» в избранном: копируют соседний .post-box
document.querySelectorAll(".btn-copy-fav").forEach((btn) => {
  btn.addEventListener("click", async () => {
    const item = btn.closest(".list-item");
    const box = item ? item.querySelector(".post-box") : null;
    if (!box) return;
    await copyText(box.innerText);
    const original = btn.textContent;
    btn.textContent = "Скопировано ✅";
    setTimeout(() => {
      btn.textContent = original;
    }, 1500);
  });
});

// Отложка: нельзя выбрать прошлое + подсказываем минимум (+2 минуты)
if (publishTime) {
  const pad = (n) => String(n).padStart(2, "0");
  const now = new Date(Date.now() + 2 * 60 * 1000);
  const minStr =
    now.getFullYear() + "-" + pad(now.getMonth() + 1) + "-" + pad(now.getDate()) +
    "T" + pad(now.getHours()) + ":" + pad(now.getMinutes());
  publishTime.min = minStr;
}

// ============================================================
// Редактирование поста (кнопка-карандаш ✏️)
// ============================================================
// Как это работает: блок поста меняется на текстовое поле,
// правим текст, жмём «Сохранить» — и новый текст расходится
// по всем скрытым полям (.js-current-post). После этого кнопки
// «Опубликовать», «Запланировать» и «В избранное» отправят
// именно отредактированный вариант.
const editBtn = document.getElementById("edit-btn");
const cancelEditBtn = document.getElementById("cancel-edit-btn");
const editMsg = document.getElementById("edit-msg");

// Кнопки есть только на странице с готовым постом
if (editBtn && postBox) {
  let editing = false;
  let editor = null;

  // На время правок блокируем остальные кнопки в блоке поста,
  // чтобы случайно не отправить старый текст.
  const toggleResultButtons = (disabled) => {
    document.querySelectorAll("#result button").forEach((btn) => {
      if (btn !== editBtn && btn !== cancelEditBtn) btn.disabled = disabled;
    });
  };

  const showEditMsg = (text) => {
    if (!editMsg) return;
    editMsg.textContent = text;
    editMsg.classList.add("visible");
    setTimeout(() => editMsg.classList.remove("visible"), 2000);
  };

  editBtn.addEventListener("click", () => {
    if (!editing) {
      // Входим в режим правок: блок поста -> текстовое поле
      editing = true;
      editor = document.createElement("textarea");
      editor.className = "post-box post-editor";
      editor.value = postBox.innerText;
      editor.rows = Math.max(6, editor.value.split("\n").length + 2);
      postBox.replaceWith(editor);
      editor.focus();
      editBtn.textContent = "💾 Сохранить";
      if (cancelEditBtn) cancelEditBtn.hidden = false;
      toggleResultButtons(true);
    } else {
      // Сохраняем: текст обратно в блок + разносим по формам
      const newText = editor.value.trim();
      if (!newText) {
        showEditMsg("Пост пустой — напиши хоть что-нибудь ✏️");
        return;
      }
      postBox.textContent = newText; // textContent, а не innerHTML — безопасно
      editor.replaceWith(postBox);
      document.querySelectorAll(".js-current-post").forEach((field) => {
        field.value = newText;
      });
      editing = false;
      editor = null;
      editBtn.textContent = "✏️ Редактировать";
      if (cancelEditBtn) cancelEditBtn.hidden = true;
      toggleResultButtons(false);
      showEditMsg("Изменения сохранены ✅");
    }
  });

  if (cancelEditBtn) {
    cancelEditBtn.addEventListener("click", () => {
      if (!editing || !editor) return;
      editor.replaceWith(postBox); // старый текст в postBox не тронут
      editing = false;
      editor = null;
      editBtn.textContent = "✏️ Редактировать";
      cancelEditBtn.hidden = true;
      toggleResultButtons(false);
    });
  }
}
