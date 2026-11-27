package esgram.local;

import android.app.*;
import android.content.*;
import android.net.Uri;
import android.os.Bundle;
import android.view.View;
import android.widget.*;
import java.io.*;
import java.nio.charset.StandardCharsets;
import java.util.Arrays;

/** Device-local reader for chat.txt exports received from the business bot. */
public final class ArchiveActivity extends Activity {
    LinearLayout page;
    File dir;
    boolean reading;
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        dir = new File(getFilesDir(), "esgram-archives");
        dir.mkdirs();
        home();
    }
    void button(String title, View.OnClickListener click) {
        Button b = new Button(this);
        b.setText(title);
        b.setOnClickListener(click);
        page.addView(b);
    }
    void layout(String title) {
        setTitle(title);
        ScrollView scroll = new ScrollView(this);
        page = new LinearLayout(this);
        page.setOrientation(1);
        int pad = (int)(16 * getResources().getDisplayMetrics().density);
        page.setPadding(pad,pad,pad,pad);
        scroll.addView(page);
        setContentView(scroll);
    }
    void home() {
        reading = false;
        layout("Удалённые чаты · Esgram");
        button("Импортировать TXT от @esgrambot", v -> {
            Intent pick = new Intent(Intent.ACTION_OPEN_DOCUMENT);
            pick.setType("text/plain");
            pick.addCategory(Intent.CATEGORY_OPENABLE);
            startActivityForResult(pick, 1);
        });
        button("Открыть @esgrambot", v -> startActivity(new Intent(Intent.ACTION_VIEW, Uri.parse("https://t.me/esgrambot"))));
        File[] files = dir.listFiles((d,n) -> n.endsWith(".txt"));
        if (files == null) return;
        Arrays.sort(files, (a,b) -> Long.compare(b.lastModified(), a.lastModified()));
        for (File file : files) button(file.getName(), v -> open(file));
    }
    @Override protected void onActivityResult(int request, int result, Intent data) {
        super.onActivityResult(request,result,data);
        if (request != 1 || result != RESULT_OK || data == null || data.getData() == null) return;
        Uri uri = data.getData();
        EditText name = new EditText(this);
        name.setHint("Имя собеседника");
        new AlertDialog.Builder(this).setTitle("Название архива").setView(name)
            .setNegativeButton("Отмена", null).setPositiveButton("Сохранить", (d,w) -> {
                String label = name.getText().toString().trim().replaceAll("[^\\p{L}\\p{N} _-]", "_");
                File file = new File(dir, (label.isEmpty() ? "Чат" : label.substring(0, Math.min(80,label.length()))) + "-" + System.currentTimeMillis() + ".txt");
                new Thread(() -> {
                    try (InputStream in = getContentResolver().openInputStream(uri); OutputStream out = new FileOutputStream(file)) {
                        byte[] buf = new byte[8192]; int n, total=0;
                        while ((n=in.read(buf))!=-1) {
                            if ((total+=n)>4*1024*1024) throw new IOException("Максимум 4 МБ");
                            out.write(buf,0,n);
                        }
                    } catch (Exception e) { file.delete(); error(e); return; }
                    runOnUiThread(this::home);
                }).start();
            }).show();
    }
    void error(Exception e) { runOnUiThread(() -> Toast.makeText(this, e.getMessage(), Toast.LENGTH_LONG).show()); }
    void open(File file) {
        try (BufferedReader r = new BufferedReader(new InputStreamReader(new FileInputStream(file),StandardCharsets.UTF_8))) {
            StringBuilder content = new StringBuilder(); String line;
            while ((line=r.readLine())!=null) content.append(line).append('\n');
            reading = true;
            layout(file.getName());
            button("К списку", v -> home());
            TextView t = new TextView(this); t.setTextIsSelectable(true); t.setText(content); page.addView(t);
        } catch(IOException e) { error(e); }
    }
    @Override public void onBackPressed() { if(reading) home(); else super.onBackPressed(); }
}
