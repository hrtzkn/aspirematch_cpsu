let inactivityTime = 4 * 60 * 1000; // 3 minutes
let inactivityTimer;

function resetTimer() {
    clearTimeout(inactivityTimer);

    inactivityTimer = setTimeout(() => {
        window.location.href = "/admin/logout";
    }, inactivityTime);
}

// Detect activity
window.onload = resetTimer;

document.onmousemove = resetTimer;
document.onkeypress = resetTimer;
document.onclick = resetTimer;
document.onscroll = resetTimer;