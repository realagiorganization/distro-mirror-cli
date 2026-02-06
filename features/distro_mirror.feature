Feature: Distro Mirror CLI
  The CLI builds trimmed Docker images, mirrors repos, and tracks sessions.

  Scenario: Build a distro image context
    When I build a distro image context
    Then a Dockerfile is created
    And a mirror plan is created

  Scenario: Initialize a home repo
    When I initialize a home repo
    Then the home repo is bare

  Scenario: Bundle a repository
    Given a sample git repo
    When I bundle the repository
    Then the bundle file exists

  Scenario: Create and open a session
    When I create an rdp session
    And I open the session
    Then the output includes "xfreerdp"
