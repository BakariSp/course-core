// 调后端接口。GET 只读；POST 要带页面里的 token（防 CSRF，见 studykit/web.py）。
const TOKEN = document.querySelector('meta[name="study-token"]')?.content || "";

export async function api(path, body) {
  const res = await fetch(path, body === undefined ? {} : {
    method: "POST", headers: { "Content-Type": "application/json", "X-Study-Token": TOKEN }, body: JSON.stringify(body),
  });
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
