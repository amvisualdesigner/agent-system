import React from 'react';
import { Card } from '@/components/ui/Card';

interface UserProfileFormProps {
  user?: {
    name: string;
    email: string;
    role: string;
  };
  onSave: (data: { name: string; email: string; role: string }) => void;
}

export const UserProfileForm: React.FC<UserProfileFormProps> = ({
  user = { name: '', email: '', role: 'viewer' },
  onSave,
}) => {
  const [name, setName] = React.useState(user.name);
  const [email, setEmail] = React.useState(user.email);
  const [role, setRole] = React.useState(user.role);

  const handleSubmit = (e: React.FormEvent) => {
    e.preventDefault();
    onSave({ name, email, role });
  };

  return (
    <Card>
      <form className="profile-form" onSubmit={handleSubmit}>
        <h2>User Profile</h2>
        <label>Name <input value={name} onChange={(e) => setName(e.target.value)} /></label>
        <label>Email <input type="email" value={email} onChange={(e) => setEmail(e.target.value)} /></label>
        <label>Role
          <select value={role} onChange={(e) => setRole(e.target.value)}>
            <option value="admin">Admin</option>
            <option value="editor">Editor</option>
            <option value="viewer">Viewer</option>
          </select>
        </label>
        <button type="submit">Save Profile</button>
      </form>
    </Card>
  );
};
