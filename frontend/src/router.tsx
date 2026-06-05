// 路由表。
import { createBrowserRouter, Navigate } from 'react-router-dom'
import Layout from './components/Layout'
import TaskListPage from './pages/TaskListPage'
import TaskCreatePage from './pages/TaskCreatePage'
import TaskDetailPage from './pages/TaskDetailPage'
import ConfigPage from './pages/ConfigPage'

export const router = createBrowserRouter([
  {
    path: '/',
    element: <Layout />,
    children: [
      { index: true, element: <Navigate to="/tasks" replace /> },
      { path: 'tasks', element: <TaskListPage /> },
      { path: 'tasks/new', element: <TaskCreatePage /> },
      { path: 'tasks/:id', element: <TaskDetailPage /> },
      { path: 'config', element: <ConfigPage /> },
    ],
  },
])
