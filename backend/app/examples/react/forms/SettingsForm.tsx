import React from 'react';
import { Card } from '@/components/ui/Card';

interface SettingsFormProps {
  initialValues?: Record<string, string>;
  onSubmit: (values: Record<string, string>) => void;
}

export const SettingsForm: React.FC<SettingsFormProps> = ({
  initialValues = {},
  onSubmit,
}) => {
  const [values, setValues] = React.useState(initialValues);

  const handleChange = (key: string, value: string) => {
    setValues((prev) => ({ ...prev, [key]: value }));
  };

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSubmit(values);
  };

  return (
    <Card>
      <form className="settings-form" onSubmit={handleSubmit}>
        <h2>Settings</h2>
        {Object.keys(values).map((key) => (
          <label key={key}>
            <span>{key}</span>
            <input
              value={values[key]}
              onChange={(e) => handleChange(key, e.target.value)}
            />
          </label>
        ))}
        <button type="submit">Save</button>
      </form>
    </Card>
  );
};
