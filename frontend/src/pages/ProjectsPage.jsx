import React, { useState, useEffect } from 'react';
import { Plus } from 'lucide-react';
import toast from 'react-hot-toast';
import useAppStore from '../store/appStore';
import api from '../services/api';
import ProjectList from '../components/Projects/ProjectList';
import ProjectForm from '../components/Projects/ProjectForm';

export default function ProjectsPage() {
  const setProjects = useAppStore((s) => s.setProjects);
  const [isFormOpen, setIsFormOpen] = useState(false);
  const [editingProject, setEditingProject] = useState(null);

  useEffect(() => {
    async function load() {
      try {
        const projs = await api.getProjects();
        setProjects(projs);
      } catch (err) {
        console.error('Failed to load projects:', err);
        toast.error(err?.message || 'Failed to load projects');
      }
    }
    load();
  }, [setProjects]);

  const handleEdit = (proj) => {
    setEditingProject(proj);
    setIsFormOpen(true);
  };

  const handleCreate = () => {
    setEditingProject(null);
    setIsFormOpen(true);
  };

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-6">
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-slate-100">QA Projects</h1>
          <p className="text-xs text-slate-400 mt-1">
            Manage your applications under test, repository links, and Jira project boards.
          </p>
        </div>
        <button
          onClick={handleCreate}
          className="flex items-center gap-2 px-4 py-2.5 rounded-xl bg-cyan-600 hover:bg-cyan-500 text-white text-xs font-semibold shadow-lg shadow-cyan-900/30 transition-all"
        >
          <Plus className="w-4 h-4" />
          <span>New Project</span>
        </button>
      </div>

      <ProjectList onEditProject={handleEdit} />

      <ProjectForm
        isOpen={isFormOpen}
        onClose={() => {
          setIsFormOpen(false);
          setEditingProject(null);
        }}
        initialData={editingProject}
      />
    </div>
  );
}

