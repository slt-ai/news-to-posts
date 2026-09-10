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
