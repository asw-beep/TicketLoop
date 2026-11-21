// Interview questions start closed so you can answer before you look.
// The button opens or closes all of them at once for a quick review.
document.querySelectorAll("[data-toggle-answers]").forEach((button) => {
  const answers = () => [...document.querySelectorAll("details.qa")];
  const allOpen = () => answers().every((item) => item.open);

  const sync = () => {
    button.textContent = allOpen() ? "Hide all answers" : "Show all answers";
    button.setAttribute("aria-pressed", String(allOpen()));
  };

  button.addEventListener("click", () => {
    const open = !allOpen();
    answers().forEach((item) => {
      item.open = open;
    });
    sync();
  });
  answers().forEach((item) => item.addEventListener("toggle", sync));
  sync();
});

// A printed page shows every answer.
window.addEventListener("beforeprint", () => {
  document.querySelectorAll("details").forEach((item) => {
    item.open = true;
  });
});
