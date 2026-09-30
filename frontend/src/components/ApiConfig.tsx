import React, { useEffect } from 'react';
import type { ApiConfig } from '../types/config';
import './ApiConfig.css';
import { useConfig } from '../hooks/useConfig';

type Props = {
  onSave: (config: ApiConfig) => void;
};

const ApiConfig: React.FC<Props> = ({ onSave }) => {
  const {
    config,
    update,
    save,
    resetToDefaults,
    load,
    validate,
    testConnection,
    testState,
    testError,
    saveState,
    saveError
  } = useConfig();

  const [localErrors, setLocalErrors] = React.useState<string[]>([]);

  useEffect(() => {
    (async () => {
      await load();
    })();
  }, []);

  const handleSave = async () => {
    const { valid, errors } = validate();
    if (!valid) {
      setLocalErrors(errors);
      return;
    }
    setLocalErrors([]);
    const result = await save();
    if (result.success) {
      onSave(config);
    }
  };

  const handleReset = () => {
    resetToDefaults();
    setLocalErrors([]);
  };

  return (
    <form className="api-config" onSubmit={(e) => { e.preventDefault(); handleSave(); }}>
      <div className="field">
        <label>API 地址</label>
        <input
          value={config.apiUrl}
          onChange={(e) => update({ apiUrl: e.target.value })}
          type="text"
          placeholder="https://api.deepseek.com"
        />
      </div>
      <div className="field">
        <label>API 密钥</label>
        <input
          value={config.apiKey}
          onChange={(e) => update({ apiKey: e.target.value })}
          type="password"
          placeholder="输入 API 密钥"
        />
      </div>
      <div className="field">
        <label>模型名称</label>
        <input
          value={config.model}
          onChange={(e) => update({ model: e.target.value })}
          type="text"
          placeholder="模型名称（例如 deepseek-v4-flash）"
        />
      </div>
      <div className="field inline">
        <label>Temperature</label>
        <input
          type="number"
          min={0}
          max={2}
          step={0.1}
          value={config.temperature}
          onChange={(e) => update({ temperature: parseFloat(e.target.value) || 0 })}
        />
      </div>
      <div className="field inline">
        <label>最大 Token 数</label>
        <input
          type="number"
          value={config.maxTokens}
          onChange={(e) => update({ maxTokens: parseInt(e.target.value) || 0 })}
        />
      </div>
      <div className="field inline">
        <label>代理端口</label>
        <input
          type="number"
          min={0}
          max={65535}
          value={config.proxyPort}
          onChange={(e) => update({ proxyPort: parseInt(e.target.value) || 0 })}
          placeholder="6984"
        />
        <span className="hint">0 = 不使用代理（直连）</span>
      </div>

      {localErrors.length > 0 && (
        <div className="validation-errors" aria-live="polite">
          {localErrors.map((err, idx) => (
            <div key={idx} className="error-item">{err}</div>
          ))}
        </div>
      )}

      <div className="actions">
        <button type="button" className="btn btn-primary" onClick={handleSave} disabled={saveState === 'saving'}>
          {saveState === 'saving' ? '保存中...' : saveState === 'saved' ? '已保存' : '保存'}
        </button>
        <button type="button" className="btn btn-secondary" onClick={() => testConnection()} disabled={testState === 'loading'}>
          {testState === 'loading' ? '测试中...' : '测试连接'}
        </button>
        <button type="button" className="btn btn-ghost" onClick={handleReset}>恢复默认</button>
      </div>

      {saveError && <div className="status error" role="status">{saveError}</div>}
      {testError && <div className="status error" role="status">{testError}</div>}
      {testState === 'success' && <div className="status success" role="status">连接成功</div>}
      {testState === 'loading' && <div className="status loading" role="status">测试中...</div>}
    </form>
  );
};

export default ApiConfig;
