# frozen_string_literal: true

# Decision tests for fastlane/Fastfile, run without fastlane or network:
#   ruby tests/test_fastfile.rb
# The Fastfile is evaluated against stand-ins for the fastlane actions and Spaceship it uses, which
# record each call, so the tests check exactly what would be sent to Google Play / App Store Connect.

require "minitest/autorun"
require "fileutils"
require "tmpdir"

FASTFILE = File.expand_path("../fastlane/Fastfile", __dir__)

# fastlane's UI: user_error! aborts the lane.
module UI
  class Error < StandardError; end

  def self.user_error!(message)
    raise Error, message
  end

  def self.important(_message); end

  def self.success(_message); end

  def self.message(_message); end
end

# Spaceship stand-ins with the same signatures as fastlane 2.241.2, configured per test.
module Spaceship
  module ConnectAPI
    module Platform
      IOS = "IOS"
    end

    class << self
      # apps: bundle id => App; uploads: [version, build] => states; queries: filters sent to buildUploads
      attr_accessor :apps, :uploads, :uploads_error, :queries

      # Like the API: filtering happens server side, and each upload's state is an object, not a string.
      def get_build_uploads(app_id:, filter: {}, includes: nil, limit: nil, sort: nil)
        raise uploads_error if uploads_error

        queries << filter
        wanted = filter[:state].to_s.split(",")
        uploads.fetch([filter[:cfBundleShortVersionString], filter[:cfBundleVersion]], [])
               .select { |state| wanted.empty? || wanted.include?(state) }
               .map { |state| BuildUpload.new({ "state" => state, "errors" => [] }) }
      end
    end

    BuildUpload = Struct.new(:state)
    Localization = Struct.new(:locale)

    Version = Struct.new(:locales) do
      def get_app_store_version_localizations
        locales.map { |locale| Localization.new(locale) }
      end
    end

    App = Struct.new(:id, :bundle_id, :name, :primary_locale, :edit_version, :live_version) do
      # No client: keyword on purpose: lanes must rely on the token set by app_store_connect_api_key.
      def self.find(bundle_id)
        ConnectAPI.apps[bundle_id]
      end

      def get_edit_app_store_version(platform:)
        edit_version
      end

      def get_live_app_store_version(platform:)
        live_version
      end
    end
  end
end

# Evaluates the Fastfile and runs one lane with fake actions.
class Lanes
  attr_reader :calls

  def initialize(env:, play_tracks: {}, play_errors: [])
    @env = env
    @play_tracks = play_tracks # track => [version codes]
    @play_errors = play_errors.dup # errors raised by successive upload_to_play_store calls (nil = success)
    @calls = []
    @lanes = {}
    @dir = Dir.mktmpdir
    FileUtils.mkdir_p(File.join(@dir, "fastlane"))
    instance_eval(File.read(FASTFILE), File.join(@dir, "fastlane", "Fastfile"))
  end

  def run(platform, lane)
    with_env { instance_exec(&@lanes.fetch([platform, lane])) }
  end

  def metadata_dir
    File.join(@dir, "build", "play-metadata")
  end

  def cleanup
    FileUtils.rm_rf(@dir)
  end

  # fastlane DSL
  def default_platform(_name); end

  def desc(_text); end

  def platform(name)
    @platform = name
    yield
  end

  def lane(name, &block)
    @lanes[[@platform, name]] = block
  end

  # fastlane actions
  def upload_to_play_store(params)
    @calls << [:upload_to_play_store, params]
    error = @play_errors.shift
    raise error if error
  end

  def google_play_track_version_codes(package_name:, json_key_data:, track:)
    @calls << [:google_play_track_version_codes, track]
    @play_tracks.fetch(track, [])
  end

  def app_store_connect_api_key(**params)
    @calls << [:app_store_connect_api_key, params]
    { key_id: params[:key_id] }
  end

  def upload_to_testflight(params)
    @calls << [:upload_to_testflight, params]
  end

  def deliver(params)
    @calls << [:deliver, params]
  end

  def calls_to(action)
    @calls.select { |name, _| name == action }.map(&:last)
  end

  private

  def with_env
    saved = ENV.to_h
    @env.each { |key, value| ENV[key] = value }
    yield
  ensure
    ENV.replace(saved)
  end
end

module LaneHelpers
  def teardown
    @lanes&.cleanup
  end

  def lanes(env, **options)
    @lanes = Lanes.new(env: base_env.merge(env), **options)
  end
end

class AndroidLaneTest < Minitest::Test
  include LaneHelpers

  def setup
    @aab = File.join(Dir.mktmpdir, "app.aab")
    File.write(@aab, "aab")
  end

  def base_env
    {
      "RELEASE_FILE" => @aab,
      "ANDROID_PACKAGE" => "com.example.app",
      "GOOGLE_PLAY_JSON_KEY" => "{}",
      "ANDROID_VERSION_CODE" => "30201",
      "ANDROID_TRACK" => "internal",
      "ANDROID_ROLLOUT" => "100",
      "ANDROID_RELEASE_STATUS" => "completed",
      "RELEASE_NOTES" => "",
      "DRY_RUN" => "false",
    }
  end

  def duplicate
    StandardError.new("Google Api Error: apkUpgradeVersionConflict: APK specifies a version code that has already been used.")
  end

  def test_staged_production_upload_with_release_notes
    l = lanes({ "ANDROID_TRACK" => "production", "ANDROID_ROLLOUT" => "20", "RELEASE_NOTES" => "Bug fixes", "RELEASE_NOTES_LANGUAGES" => "en-US, de-DE" })
    l.run(:android, :upload)
    params = l.calls_to(:upload_to_play_store).first
    assert_equal "production", params[:track]
    assert_equal "inProgress", params[:release_status]
    assert_equal "0.2", params[:rollout]
    assert_equal @aab, params[:aab]
    assert_equal false, params[:skip_upload_changelogs]
    assert_equal true, params[:skip_upload_metadata]
    assert_equal false, params[:validate_only]
    %w[en-US de-DE].each do |locale|
      assert_equal "Bug fixes", File.read(File.join(l.metadata_dir, locale, "changelogs", "default.txt"))
    end
  end

  def test_full_rollout_has_no_fraction_and_no_changelogs_without_notes
    l = lanes({ "ANDROID_TRACK" => "production" })
    l.run(:android, :upload)
    params = l.calls_to(:upload_to_play_store).first
    assert_equal "completed", params[:release_status]
    refute params.key?(:rollout)
    refute params.key?(:metadata_path)
    assert_equal true, params[:skip_upload_changelogs]
  end

  def test_release_notes_truncated_to_play_limit
    l = lanes({ "RELEASE_NOTES" => "x" * 600 })
    l.run(:android, :upload)
    assert_equal 500, File.read(File.join(l.metadata_dir, "en-US", "changelogs", "default.txt")).length
  end

  def test_internal_track_ignores_staged_rollout
    l = lanes({ "ANDROID_TRACK" => "internal", "ANDROID_ROLLOUT" => "20" })
    l.run(:android, :upload)
    params = l.calls_to(:upload_to_play_store).first
    assert_equal "completed", params[:release_status]
    refute params.key?(:rollout)
  end

  def test_draft_has_no_rollout
    l = lanes({ "ANDROID_TRACK" => "production", "ANDROID_ROLLOUT" => "50", "ANDROID_RELEASE_STATUS" => "draft" })
    l.run(:android, :upload)
    params = l.calls_to(:upload_to_play_store).first
    assert_equal "draft", params[:release_status]
    refute params.key?(:rollout)
  end

  def test_dry_run_validates_only
    l = lanes({ "DRY_RUN" => "true" })
    l.run(:android, :upload)
    assert_equal true, l.calls_to(:upload_to_play_store).first[:validate_only]
  end

  def test_invalid_rollout
    [["0"], ["101"], ["abc"]].each do |(rollout)|
      l = lanes({ "ANDROID_ROLLOUT" => rollout })
      assert_raises(UI::Error) { l.run(:android, :upload) }
      assert_empty l.calls_to(:upload_to_play_store)
      l.cleanup
    end
  end

  def test_invalid_release_status
    l = lanes({ "ANDROID_RELEASE_STATUS" => "inProgress" })
    error = assert_raises(UI::Error) { l.run(:android, :upload) }
    assert_match(/ANDROID_RELEASE_STATUS/, error.message)
    assert_empty l.calls_to(:upload_to_play_store)
  end

  def test_duplicate_promotes_from_the_track_that_holds_the_build
    { { "internal" => [30_201] } => "internal", { "beta" => [30_201], "internal" => [30_300] } => "beta" }.each do |tracks, source|
      l = lanes({ "ANDROID_TRACK" => "production", "ANDROID_ROLLOUT" => "20", "RELEASE_NOTES" => "Release notes" }, play_tracks: tracks, play_errors: [duplicate])
      l.run(:android, :upload)
      promote = l.calls_to(:upload_to_play_store).last
      assert_equal source, promote[:track]
      assert_equal "production", promote[:track_promote_to]
      assert_equal "completed", promote[:track_promote_release_status]
      assert_equal "0.2", promote[:rollout]
      assert_equal 30_201, promote[:version_code]
      assert_equal false, promote[:skip_upload_changelogs]
      refute promote.key?(:aab)
      l.cleanup
    end
  end

  def test_duplicate_promoted_to_internal_from_another_track
    l = lanes({ "ANDROID_TRACK" => "internal" }, play_tracks: { "alpha" => [30_201] }, play_errors: [duplicate])
    l.run(:android, :upload)
    promote = l.calls_to(:upload_to_play_store).last
    assert_equal %w[alpha internal], [promote[:track], promote[:track_promote_to]]
    refute promote.key?(:rollout)
  end

  def test_duplicate_promotion_to_draft_has_no_rollout
    l = lanes({ "ANDROID_TRACK" => "production", "ANDROID_ROLLOUT" => "20", "ANDROID_RELEASE_STATUS" => "draft" },
              play_tracks: { "internal" => [30_201] }, play_errors: [duplicate])
    l.run(:android, :upload)
    promote = l.calls_to(:upload_to_play_store).last
    assert_equal "draft", promote[:track_promote_release_status]
    refute promote.key?(:rollout)
  end

  def test_duplicate_not_on_any_track
    l = lanes({ "ANDROID_TRACK" => "production" }, play_tracks: { "internal" => [30_300] }, play_errors: [duplicate])
    error = assert_raises(UI::Error) { l.run(:android, :upload) }
    assert_match(/not on the internal, alpha, beta or production track/, error.message)
    assert_equal 1, l.calls_to(:upload_to_play_store).size
  end

  def test_duplicate_already_on_target_track_updates_rollout
    { "50" => ["inProgress", "0.5"], "100" => ["completed", "1"] }.each do |rollout, (status, fraction)|
      l = lanes({ "ANDROID_TRACK" => "production", "ANDROID_ROLLOUT" => rollout }, play_tracks: { "internal" => [30_201], "production" => [30_201] }, play_errors: [duplicate])
      l.run(:android, :upload)
      update = l.calls_to(:upload_to_play_store).last
      assert_equal "production", update[:track]
      refute update.key?(:track_promote_to)
      assert_equal status, update[:release_status]
      assert_equal fraction, update[:rollout]
      assert_equal 30_201, update[:version_code]
      l.cleanup
    end
  end

  def test_duplicate_already_fully_released
    not_found = StandardError.new("Unable to find the requested release on track - 'production'")
    l = lanes({ "ANDROID_TRACK" => "production" }, play_tracks: { "production" => [30_201] }, play_errors: [duplicate, not_found])
    error = assert_raises(UI::Error) { l.run(:android, :upload) }
    assert_match(/already fully released/, error.message)
  end

  def test_duplicate_already_on_target_as_draft
    l = lanes({ "ANDROID_TRACK" => "beta", "ANDROID_RELEASE_STATUS" => "draft" }, play_tracks: { "beta" => [30_201] }, play_errors: [duplicate])
    assert_raises(UI::Error) { l.run(:android, :upload) }
    assert_equal 1, l.calls_to(:upload_to_play_store).size
  end

  def test_check_access
    l = lanes({}, play_tracks: { "production" => [30_100] })
    l.run(:android, :check_access)
    assert_equal ["production"], l.calls_to(:google_play_track_version_codes)
  end

  def test_other_errors_are_not_swallowed
    l = lanes({}, play_errors: [StandardError.new("403 The caller does not have permission")])
    assert_raises(StandardError) { l.run(:android, :upload) }
    assert_empty l.calls_to(:google_play_track_version_codes)

    l = lanes({ "ANDROID_VERSION_CODE" => "" }, play_errors: [duplicate])
    assert_raises(StandardError) { l.run(:android, :upload) }
    assert_empty l.calls_to(:google_play_track_version_codes)
  end
end

class IosLaneTest < Minitest::Test
  include LaneHelpers

  def setup
    @ipa = File.join(Dir.mktmpdir, "app.ipa")
    File.write(@ipa, "ipa")
    @version = Spaceship::ConnectAPI::Version.new(%w[en-US fr-FR])
    @app = Spaceship::ConnectAPI::App.new("app-1", "com.example.app", "App", "en-US", @version, nil)
    Spaceship::ConnectAPI.apps = { "com.example.app" => @app }
    Spaceship::ConnectAPI.uploads = {}
    Spaceship::ConnectAPI.uploads_error = nil
    Spaceship::ConnectAPI.queries = []
  end

  def base_env
    {
      "RELEASE_FILE" => @ipa,
      "IOS_BUNDLE_ID" => "com.example.app",
      "IOS_VERSION" => "3.2.1",
      "IOS_BUILD" => "321",
      "IOS_TARGET" => "testflight",
      "ASC_KEY_ID" => "KEY",
      "ASC_ISSUER_ID" => "ISSUER",
      "ASC_KEY_P8" => "p8",
      "RELEASE_NOTES" => "",
      "DRY_RUN" => "false",
    }
  end

  def test_dry_run_does_not_contact_app_store_connect
    l = lanes({ "DRY_RUN" => "true" })
    l.run(:ios, :upload)
    assert_empty l.calls
  end

  def test_invalid_target_is_not_treated_as_testflight
    l = lanes({ "IOS_TARGET" => "app-store" })
    error = assert_raises(UI::Error) { l.run(:ios, :upload) }
    assert_match(/IOS_TARGET/, error.message)
    assert_empty l.calls
  end

  def test_new_build_to_testflight
    l = lanes({})
    l.run(:ios, :upload)
    upload = l.calls_to(:upload_to_testflight).first
    assert_equal @ipa, upload[:ipa]
    assert_equal true, upload[:skip_waiting_for_build_processing]
    assert_nil upload[:changelog]
    assert_empty l.calls_to(:deliver)
  end

  def test_new_build_with_notes_waits_for_processing
    l = lanes({ "RELEASE_NOTES" => "What's new" })
    l.run(:ios, :upload)
    upload = l.calls_to(:upload_to_testflight).first
    assert_equal "What's new", upload[:changelog]
    assert_equal false, upload[:skip_waiting_for_build_processing]
  end

  def test_build_already_uploaded_is_not_uploaded_again
    Spaceship::ConnectAPI.uploads = { %w[3.2.1 321] => %w[COMPLETE] }
    l = lanes({})
    l.run(:ios, :upload)
    assert_empty l.calls_to(:upload_to_testflight)
    assert_equal [{ cfBundleShortVersionString: "3.2.1", cfBundleVersion: "321", state: "PROCESSING,COMPLETE" }], Spaceship::ConnectAPI.queries
  end

  def test_export_compliance_answer
    { {} => false, { "IOS_USES_NON_EXEMPT_ENCRYPTION" => "true" } => true }.each do |env, expected|
      l = lanes(env.merge("IOS_TARGET" => "app-store-review"))
      l.run(:ios, :upload)
      assert_equal expected, l.calls_to(:upload_to_testflight).first[:uses_non_exempt_encryption]
      assert_equal expected, l.calls_to(:deliver).first[:submission_information][:export_compliance_uses_encryption]
      l.cleanup
    end
  end

  def test_earlier_failed_upload_is_retried
    Spaceship::ConnectAPI.uploads = { %w[3.2.1 321] => %w[FAILED] }
    l = lanes({})
    l.run(:ios, :upload)
    assert_equal @ipa, l.calls_to(:upload_to_testflight).first[:ipa]
  end

  def test_build_already_uploaded_with_notes_updates_what_to_test
    Spaceship::ConnectAPI.uploads = { %w[3.2.1 321] => %w[PROCESSING] }
    l = lanes({ "RELEASE_NOTES" => "What's new" })
    l.run(:ios, :upload)
    distribute = l.calls_to(:upload_to_testflight).first
    assert_equal true, distribute[:distribute_only]
    assert_equal true, distribute[:skip_submission]
    assert_equal ["ios", "3.2.1", "321", "What's new"], distribute.values_at(:app_platform, :app_version, :build_number, :changelog)
    refute distribute.key?(:ipa)
  end

  def test_lookup_failure_falls_back_to_upload
    Spaceship::ConnectAPI.uploads_error = StandardError.new("503")
    l = lanes({})
    l.run(:ios, :upload)
    assert_equal @ipa, l.calls_to(:upload_to_testflight).first[:ipa]
  end

  def test_app_store_review_with_notes_uses_the_versions_languages
    # RELEASE_NOTES_LANGUAGES is for Google Play; App Store notes follow the version's own languages.
    l = lanes({ "IOS_TARGET" => "app-store-review", "RELEASE_NOTES" => "What's new", "RELEASE_NOTES_LANGUAGES" => "en-US,de-DE" })
    l.run(:ios, :upload)
    assert_equal false, l.calls_to(:upload_to_testflight).first[:skip_waiting_for_build_processing]
    deliver = l.calls_to(:deliver).first
    assert_equal({ "en-US" => "What's new", "fr-FR" => "What's new" }, deliver[:release_notes])
    assert_equal %w[3.2.1 321], deliver.values_at(:app_version, :build_number)
    assert_equal [true, true, false, false], deliver.values_at(:submit_for_review, :skip_binary_upload, :skip_metadata, :automatic_release)
  end

  def test_app_store_review_of_existing_build_without_notes
    Spaceship::ConnectAPI.uploads = { %w[3.2.1 321] => %w[COMPLETE] }
    l = lanes({ "IOS_TARGET" => "app-store-review" })
    l.run(:ios, :upload)
    distribute = l.calls_to(:upload_to_testflight).first
    assert_equal true, distribute[:distribute_only]
    assert_nil distribute[:changelog]
    deliver = l.calls_to(:deliver).first
    assert_nil deliver[:release_notes]
    # deliver sets the release type while updating metadata, so metadata is never skipped.
    assert_equal [false, false], deliver.values_at(:skip_metadata, :automatic_release)
  end

  def test_whats_new_language_fallbacks
    @app.edit_version = nil
    @app.live_version = Spaceship::ConnectAPI::Version.new(%w[es-ES])
    l = lanes({ "IOS_TARGET" => "app-store-review", "RELEASE_NOTES" => "Notes" })
    l.run(:ios, :upload)
    assert_equal({ "es-ES" => "Notes" }, l.calls_to(:deliver).first[:release_notes])
    l.cleanup

    @app.live_version = nil
    l = lanes({ "IOS_TARGET" => "app-store-review", "RELEASE_NOTES" => "Notes" })
    l.run(:ios, :upload)
    assert_equal({ "en-US" => "Notes" }, l.calls_to(:deliver).first[:release_notes])
  end

  def test_check_access
    l = lanes({})
    l.run(:ios, :check_access)
    assert_equal 1, l.calls_to(:app_store_connect_api_key).size

    Spaceship::ConnectAPI.apps = {}
    assert_raises(UI::Error) { lanes({}).run(:ios, :check_access) }
  end

  def test_unknown_app
    Spaceship::ConnectAPI.apps = {}
    l = lanes({})
    assert_raises(UI::Error) { l.run(:ios, :upload) }
    assert_empty l.calls_to(:upload_to_testflight)
  end
end
