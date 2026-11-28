import { inject } from '@angular/core';
import { CanActivateFn, Router, Routes } from '@angular/router';

import { AuthService } from './core/auth.service';

/** Send people who are not signed in to the sign-in page, and bring them back afterwards. */
export const authGuard: CanActivateFn = (_route, state) => {
  const auth = inject(AuthService);
  const router = inject(Router);
  if (auth.signedIn() && !auth.expired()) return true;
  return router.createUrlTree(['/sign-in'], { queryParams: { returnUrl: state.url } });
};

export const routes: Routes = [
  {
    path: 'sign-in',
    title: 'Sign in · MLOps Platform',
    loadComponent: () => import('./features/sign-in/sign-in').then((m) => m.SignIn),
  },
  {
    path: '',
    canActivate: [authGuard],
    children: [
      {
        path: '',
        pathMatch: 'full',
        title: 'Overview · MLOps Platform',
        loadComponent: () => import('./features/overview/overview').then((m) => m.Overview),
      },
      {
        path: 'models',
        title: 'Models · MLOps Platform',
        loadComponent: () => import('./features/models/models').then((m) => m.Models),
      },
      {
        path: 'models/:id',
        title: 'Model · MLOps Platform',
        loadComponent: () =>
          import('./features/model-detail/model-detail').then((m) => m.ModelDetail),
      },
      {
        path: 'deployments',
        title: 'Deployments · MLOps Platform',
        loadComponent: () =>
          import('./features/deployments/deployments').then((m) => m.Deployments),
      },
      {
        path: 'deployments/:id',
        title: 'Deployment · MLOps Platform',
        loadComponent: () =>
          import('./features/deployments/deployments').then((m) => m.Deployments),
      },
    ],
  },
  {
    path: '**',
    title: 'Not found · MLOps Platform',
    loadComponent: () => import('./features/not-found/not-found').then((m) => m.NotFound),
  },
];
