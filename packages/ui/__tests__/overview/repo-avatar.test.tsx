import { render } from "@testing-library/react";
import { describe, expect, it } from "vitest";
import { RepoAvatar, githubOwnerFromRemote } from "../../src/overview/repo-avatar.js";

describe("githubOwnerFromRemote", () => {
  it("reads the owner out of every remote form git actually writes", () => {
    expect(githubOwnerFromRemote("https://github.com/repowise-dev/repowise.git")).toBe(
      "repowise-dev",
    );
    expect(githubOwnerFromRemote("https://github.com/repowise-dev/repowise")).toBe(
      "repowise-dev",
    );
    expect(githubOwnerFromRemote("git@github.com:repowise-dev/repowise.git")).toBe(
      "repowise-dev",
    );
    expect(githubOwnerFromRemote("ssh://git@github.com/repowise-dev/repowise.git")).toBe(
      "repowise-dev",
    );
    expect(githubOwnerFromRemote("https://token@github.com/repowise-dev/repowise")).toBe(
      "repowise-dev",
    );
  });

  it("returns null for hosts whose avatars we cannot resolve", () => {
    // Not a rejection of these hosts — just an honest "we have no image", which
    // is the signal to render initials rather than request a URL that 404s.
    expect(githubOwnerFromRemote("https://gitlab.com/group/project.git")).toBeNull();
    expect(githubOwnerFromRemote("git@bitbucket.org:team/repo.git")).toBeNull();
    expect(githubOwnerFromRemote("https://github.com/repowise-dev")).toBeNull();
    expect(githubOwnerFromRemote("")).toBeNull();
    expect(githubOwnerFromRemote(null)).toBeNull();
    expect(githubOwnerFromRemote(undefined)).toBeNull();
  });
});

describe("RepoAvatar", () => {
  it("makes no network request when the repo has no GitHub remote", () => {
    // The point of the guard: a local-first install should not call github.com
    // just because a page rendered.
    const { container } = render(<RepoAvatar name="my-repo" remoteUrl={null} />);

    expect(container.querySelector("img")).toBeNull();
    expect(container.textContent).toBe("MY");
  });

  it("layers the avatar over initials rather than swapping on error", () => {
    // The fallback must survive with JavaScript disabled, because this sits at
    // the top of a server-rendered page. Both layers are always in the markup.
    const { container } = render(
      <RepoAvatar name="repowise" remoteUrl="git@github.com:repowise-dev/repowise.git" />,
    );

    const img = container.querySelector("img");
    expect(img?.getAttribute("src")).toContain("avatars.githubusercontent.com/repowise-dev");
    expect(container.textContent).toBe("RE");
  });

  it("degrades to a placeholder for a name with no alphanumerics", () => {
    const { container } = render(<RepoAvatar name="---" />);

    expect(container.textContent).toBe("?");
  });
});
