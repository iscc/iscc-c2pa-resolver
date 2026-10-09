// Show the live service status in the page header. Same-origin request only.
fetch("/v1/services/status")
  .then((response) => response.json())
  .then((body) => {
    const element = document.getElementById("status");
    element.dataset.state = body.status;
    element.textContent = body.status === "ok" ? "Operational" : "Degraded";
  })
  .catch(() => {
    document.getElementById("status").textContent = "Status unknown";
  });
