"""Apply Esgram UI to the pinned Telegram Android checkout before building."""
from pathlib import Path
import subprocess
import sys

root = Path(sys.argv[1])
revision = "21f7d109da6ab3506c9e9577f98c6a38771ab335"
if subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() != revision:
    raise SystemExit("Use Telegram Android revision " + revision)
src = root / "TMessagesProj/src/main"
ui = src / "java/org/telegram/ui"

def insert(file, code):
    text = file.read_text(encoding="utf8")
    marker = "ActionBarMenu menu = actionBar.createMenu();"
    if text.count(marker) != 1:
        raise SystemExit("Unexpected menu layout in " + str(file))
    if code not in text:
        file.write_text(text.replace(marker, marker + "\n" + code), encoding="utf8")

insert(ui / "ChatActivity.java", '''        if (dialog_id > 0 && currentEncryptedChat == null) {
            menu.addItem(9901, R.drawable.esgram_mark).setOnClickListener(v -> {
                android.app.AlertDialog.Builder actions = new android.app.AlertDialog.Builder(getParentActivity());
                actions.setTitle("Esgram · Этот чат");
                actions.setItems(new String[]{"Действия с собеседником ▸", "Стиль текста ▸"}, (d, group) -> {
                    String[] labels = group == 0 ? new String[]{"Заглушить", "Снять мут", "Переключить клон"}
                            : new String[]{"Обычный", "Пират", "Шёпот", "Капс"};
                    String[] commands = group == 0 ? new String[]{".мут", ".размут", ".клон"}
                            : new String[]{".обычный", ".пират", ".шепот", ".капс"};
                    new android.app.AlertDialog.Builder(getParentActivity()).setItems(labels, (sub, i) -> {
                        if (chatActivityEnterView != null) chatActivityEnterView.setFieldText(commands[i]);
                    }).show();
                }).show();
            });
        }''')
insert(ui / "DialogsActivity.java", '''        if (!onlySelect && folderId == 0) {
            View esgramButton = menu.addItem(9901, R.drawable.esgram_mark);
            esgramButton.setContentDescription("Настройки Esgram");
            Runnable openBot = () -> org.telegram.messenger.browser.Browser.openUrl(getParentActivity(), "https://t.me/esgrambot");
            esgramButton.setOnClickListener(v -> new android.app.AlertDialog.Builder(getParentActivity())
                    .setTitle("Настройки Esgram").setItems(new String[]{"Открыть @esgrambot"}, (d, i) -> openBot.run()).show());
            android.content.SharedPreferences prefs = esgramButton.getContext().getSharedPreferences("esgram", 0);
            esgramButton.post(() -> {
                if (getParentActivity() == null || !esgramButton.isShown() || prefs.getBoolean("intro_seen", false)) return;
                new android.app.AlertDialog.Builder(getParentActivity()).setTitle("Добро пожаловать в Esgram")
                    .setMessage("Esgram использует Telegram API. Для мута, клона и стилей в личных чатах подключите @esgrambot в настройках Telegram для бизнеса.")
                    .setPositiveButton("Открыть бота", (d, w) -> openBot.run()).setNegativeButton("Закрыть", null)
                    .setOnDismissListener(d -> prefs.edit().putBoolean("intro_seen", true).apply()).show();
            });
        }''')
(src / "res/drawable/esgram_mark.xml").write_text('''<vector xmlns:android="http://schemas.android.com/apk/res/android" android:width="24dp" android:height="24dp" android:viewportWidth="24" android:viewportHeight="24">
<path android:fillColor="#159C69" android:pathData="M3,3h18v18H3z"/>
<path android:fillColor="#FFFFFF" android:pathData="M7,6h10v3H10v2h6v2h-6v2h7v3H7z"/>
</vector>''', encoding="utf8")

