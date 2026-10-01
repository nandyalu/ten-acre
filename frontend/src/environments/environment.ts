/** Swapped for environment.public.ts by the `public` build configuration
 * (angular.json's fileReplacements) — this is the only thing that changes
 * between the live operator build and the static public build. Two things
 * read `staticSite`: static-data.interceptor.ts, and the Decisions page's
 * pass-in-progress card, which the public build never polls. */
export const environment = {
  staticSite: false,
  /** Unused here: the live build talks to its own backend. It exists so both
   * environment files have one shape, since fileReplacements swaps them. */
  snapshotRoot: '/data',
};
