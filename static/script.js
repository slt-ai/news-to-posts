// ============================================================
// Скрипт кнопки «Скопировать пост»
// ============================================================

// Находим нужные элементы на странице
const copyBtn = document.getElementById("copy-btn");
const copyMsg = document.getElementById("copy-msg");
const postBox = document.getElementById("generated-post");
const form = document.getElementById("post-form");
const submitBtn = document.getElementById("submit-btn");

// Показываем состояние «готовим пост…», пока модель пишет текст.
// Так пользователь понимает, что страница не зависла.
if (form && submitBtn) {
  form.addEventListener("submit", () => {
    const original = submitBtn.textContent;
    submitBtn.textContent = "Готовим пост для тебя… ✍️";
    submitBtn.disabled = true;
    submitBtn.classList.add("loading");
  });
}

// Кнопка и сообщение существуют только после генерации поста
if (copyBtn && copyMsg && postBox) {
  // При нажатии кнопки копируем текст поста в буфер обмена
  copyBtn.addEventListener("click", async () => {
    const text = postBox.innerText; // текст без HTML-разметки

    try {
      // Современный способ копирования (работает на localhost)
      await navigator.clipboard.writeText(text);
    } catch {
      // Запасной вариант для старых браузеров или отказа в доступе:
      // создаём скрытое текстовое поле и копируем из него
      const tempArea = document.createElement("textarea");
      tempArea.value = text;
      document.body.appendChild(tempArea);
      tempArea.select();
      document.execCommand("copy");
      tempArea.remove();
    }

    // Показываем сообщение «Пост скопирован»
    copyMsg.classList.add("visible");

    // Через 2 секунды прячем сообщение обратно
    setTimeout(() => {
      copyMsg.classList.remove("visible");
    }, 2000);
  });
}