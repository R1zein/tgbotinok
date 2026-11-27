"""Apply Esgram UI to the pinned Telegram Android checkout before building."""
from pathlib import Path
import subprocess
import re
import sys

root = Path(sys.argv[1])
revision = "21f7d109da6ab3506c9e9577f98c6a38771ab335"
if subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip() != revision:
    raise SystemExit("Use Telegram Android revision " + revision)
src = root / "TMessagesProj/src/main"
ui = src / "java/org/telegram/ui"

# Keep credentials out of the tracked Android sources. Gradle generates BuildConfig.
build_vars = src / "java/org/telegram/messenger/BuildVars.java"
definitions = build_vars.read_text(encoding="utf8")
for field, java_type in [("APP_ID", "int"), ("APP_HASH", "String")]:
    pattern = rf"(public static {java_type} {field} = )[^;]+;"
    definitions, count = re.subn(pattern, rf"\g<1>BuildConfig.ESGRAM_{field};", definitions)
    if count != 1:
        raise SystemExit("Unexpected Telegram credential declaration: " + field)
module_build = root / "TMessagesProj/build.gradle"
gradle_text = module_build.read_text(encoding="utf8")
settings = Path(__file__).with_name("credentials.gradle").read_text(encoding="utf8")
if settings not in gradle_text:
    module_build.write_text(gradle_text.rstrip() + "\n\n" + settings, encoding="utf8")
build_vars.write_text(definitions, encoding="utf8")

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
                    .setTitle("Настройки Esgram").setItems(new String[]{"Удалённые чаты", "Настройки прокси", "Открыть @esgrambot"}, (d, i) -> {
                        if (i == 0) getParentActivity().startActivity(new android.content.Intent(getParentActivity(), esgram.local.ArchiveActivity.class));
                        else if (i == 1) presentFragment(new ProxyListActivity());
                        else openBot.run();
                    }).show());
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
activity = src / "java/esgram/local/ArchiveActivity.java"
activity.parent.mkdir(parents=True, exist_ok=True)
activity.write_bytes(Path(__file__).with_name("ArchiveActivity.java").read_bytes())
manifest = src / "AndroidManifest.xml"
content = manifest.read_text(encoding="utf8")
if "esgram.local.ArchiveActivity" not in content:
    if "</application>" not in content:
        raise SystemExit("Missing application element in Android manifest")
    manifest.write_text(content.replace("</application>", '<activity android:name="esgram.local.ArchiveActivity" android:exported="false" />\n    </application>'), encoding="utf8")

# Keep the upstream Java namespace; use a separate Android installation ID.
properties = root / "gradle.properties"
props = properties.read_text(encoding="utf8")
properties.write_text(re.sub(r"(?m)^APP_PACKAGE=.*$", "APP_PACKAGE=app.esgram.messenger", props), encoding="utf8")
for path in [manifest, *(root / "TMessagesProj/config").rglob("AndroidManifest*.xml")]:
    xml = path.read_text(encoding="utf8")
    xml = re.sub(r'android:label="@string/AppName(?:Beta)?"', 'android:label="Esgram"', xml)
    xml = re.sub(r'(android:(?:icon|roundIcon))="@mipmap/(?:ic|icon_\d+)_launcher(?:_round)?"',
                 r'\1="@drawable/esgram_mark"', xml)
    path.write_text(xml, encoding="utf8")

