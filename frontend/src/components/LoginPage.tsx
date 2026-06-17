import { Activity, AlertCircle, LogIn } from "lucide-react";
import { type FormEvent, useState } from "react";

import { AuthError, login, saveAuth } from "../api/authApi";

type LoginPageProps = {
  onLogin: (token: string, username: string) => void;
};

export function LoginPage({ onLogin }: LoginPageProps) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function handleSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!username.trim() || !password || loading) return;

    setLoading(true);
    setError(null);

    try {
      const result = await login(username.trim(), password);
      saveAuth(result.token, result.username);
      onLogin(result.token, result.username);
    } catch (err) {
      if (err instanceof AuthError) {
        setError(err.message);
      } else {
        setError("网络错误，请检查连接后重试");
      }
    } finally {
      setLoading(false);
    }
  }

  return (
    <div className="login-root">
      <div className="login-card">
        <div className="login-brand">
          <div className="login-brand-icon">
            <Activity size={18} color="#fff" aria-hidden="true" />
          </div>
          <div>
            <h1>智能 OnCall 运维平台</h1>
            <p>Agent Gateway · 智能体运维中枢</p>
          </div>
        </div>

        <form className="login-form" onSubmit={handleSubmit} noValidate>
          <div className="login-field">
            <label className="login-label" htmlFor="login-username">
              用户名
            </label>
            <input
              id="login-username"
              className="login-input"
              type="text"
              placeholder="请输入用户名"
              autoComplete="username"
              autoFocus
              value={username}
              onChange={(e) => setUsername(e.target.value)}
              disabled={loading}
            />
          </div>

          <div className="login-field">
            <label className="login-label" htmlFor="login-password">
              密码
            </label>
            <input
              id="login-password"
              className="login-input"
              type="password"
              placeholder="请输入密码"
              autoComplete="current-password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              disabled={loading}
            />
          </div>

          {error && (
            <div className="login-error" role="alert">
              <AlertCircle size={15} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
              {error}
            </div>
          )}

          <button
            className="login-submit"
            type="submit"
            disabled={loading || !username.trim() || !password}
          >
            {loading ? (
              "登录中..."
            ) : (
              <>
                <LogIn size={15} aria-hidden="true" style={{ marginRight: 6, verticalAlign: "middle" }} />
                登录
              </>
            )}
          </button>
        </form>
      </div>
    </div>
  );
}
