import React, { useState, useEffect, useCallback } from 'react';
import { useParams, useNavigate } from 'react-router-dom';
import { PieChart } from 'lucide-react';
import toast from 'react-hot-toast';
import useAppStore from '../store/appStore';
import api from '../services/api';
import SummaryReport from '../components/Reports/SummaryReport';

export default function SummaryPage() {
  const { projectId } = useParams();
  const navigate = useNavigate();

  const projects = useAppStore((s) => s.projects);
  const setProjects = useAppStore((s) => s.setProjects);

  const [summaryData, setSummaryData] = useState(null);
  const [loading, setLoading] = useState(true);

  const loadSummary = useCallback(async (targetId) => {
    setLoading(true);
    try {
      const data = await api.getSummary(targetId);
      setSummaryData(data);
    } catch (err) {
      toast.error(err.message || 'Failed to load summary report');
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    async function init() {
      try {
        const projs = await api.getProjects();
        setProjects(projs);
        const activeId = projectId || (projs[0] ? projs[0].id : null);
        if (activeId) {
          await loadSummary(activeId);
        } else {
          setLoading(false);
        }
      } catch (err) {
        toast.error(err.message || 'Failed to load projects');
        setLoading(false);
      }
    }
    init();
  }, [projectId, setProjects, loadSummary]);

  const handleProjectChange = (id) => {
    navigate(`/summary/${id}`);
  };

  return (
    <div className="p-8 max-w-7xl mx-auto space-y-6">
      {/* Top selector */}
      <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          <div className="p-2.5 rounded-xl bg-cyan-950 text-cyan-400 border border-cyan-800">
            <PieChart className="w-6 h-6" />
          </div>
          <div>
            <h1 className="text-2xl font-bold text-slate-100">QA Summary & Coverage Report</h1>
            <p className="text-xs text-slate-400">
              Module breakdown, resolution rates, defect statistics, and regression health.
            </p>
          </div>
        </div>

        {projects.length > 0 && (
          <div className="flex items-center gap-2">
            <span className="text-xs text-slate-400">Target Project:</span>
            <select
              value={projectId || (projects[0] && projects[0].id)}
              onChange={(e) => handleProjectChange(e.target.value)}
              className="bg-slate-900 border border-slate-800 rounded-xl text-xs text-slate-200 px-3 py-2 focus:border-cyan-500 focus:outline-none"
            >
              {projects.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      {loading ? (
        <div className="p-16 text-center text-slate-500">
          <div className="w-8 h-8 border-2 border-cyan-500 border-t-transparent rounded-full animate-spin mx-auto mb-3" />
          Generating Executive Summary Report...
        </div>
      ) : summaryData ? (
        <SummaryReport
          data={summaryData}
          onRefresh={() => loadSummary(projectId || (projects[0] && projects[0].id))}
        />
      ) : (
        <div className="p-16 text-center text-slate-500 border border-dashed border-slate-800 rounded-2xl">
          No projects available to generate summary. Please create a project first.
        </div>
      )}
    </div>
  );
}
